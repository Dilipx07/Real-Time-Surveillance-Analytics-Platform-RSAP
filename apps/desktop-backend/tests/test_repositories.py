from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.crypto import FieldCipher
from app.database import Database
from app.repositories import AnalyticsRepository, CameraRepository, SessionRepository, SyncQueueRepository, iso
from app.schemas import AnalyticsEventCreate, CameraCreate, CameraUpdate, LocalSession


@pytest.fixture
async def repositories(settings):
    database = Database(settings)
    await database.migrate()
    cipher = FieldCipher(settings.field_encryption_key_bytes)
    yield (
        database,
        CameraRepository(database, cipher, 3),
        AnalyticsRepository(database, 3),
        SyncQueueRepository(database, 30, 3),
        SessionRepository(database, cipher),
    )
    await database.close()


@pytest.mark.asyncio
async def test_camera_secret_is_encrypted_identity_is_canonical_and_mutation_queues_atomically(repositories):
    database, cameras, _, queue, _ = repositories
    created = await cameras.create(CameraCreate(
        name="Gate", stream_url="rtsp://operator:secret@camera/live", stream_type="rtsp"
    ), 2)
    row = await database.read(lambda connection: connection.execute(
        "SELECT stream_url_encrypted FROM local_cameras WHERE id=?", (created["id"],)
    ).fetchone())
    queued = await database.read(lambda connection: connection.execute(
        "SELECT payload_json FROM sync_queue WHERE logical_key=?", (f"camera:{created['id']}",)
    ).fetchone())
    assert "secret" not in row["stream_url_encrypted"]
    assert f'"id":"{created["id"]}"' in queued["payload_json"]
    assert await queue.count() == 1


@pytest.mark.asyncio
async def test_max_camera_limit_is_transactional_under_concurrency(repositories):
    _, cameras, _, _, _ = repositories
    payloads = [CameraCreate(name=f"C{i}", stream_url=str(i), stream_type="webcam") for i in range(2)]
    results = await asyncio.gather(
        *(cameras.create(payload, 1) for payload in payloads), return_exceptions=True
    )
    assert sum(isinstance(item, dict) for item in results) == 1
    assert sum(isinstance(item, ValueError) for item in results) == 1


@pytest.mark.asyncio
async def test_active_camera_lease_is_preserved_and_successor_retains_mutation(repositories):
    database, cameras, _, queue, _ = repositories
    created = await cameras.create(CameraCreate(name="Old", stream_url="0", stream_type="webcam"), 2)
    first = (await queue.claim("worker-a", 1))[0]
    await cameras.update(created["id"], CameraUpdate(name="New"))
    rows = await database.read(lambda connection: connection.execute(
        "SELECT id,state,version,payload_json,predecessor_id FROM sync_queue ORDER BY version"
    ).fetchall())
    assert len(rows) == 2
    assert rows[0]["state"] == "inflight"
    assert rows[1]["predecessor_id"] == rows[0]["id"]
    assert '"name":"New"' in rows[1]["payload_json"]
    assert await queue.claim("worker-b", 1) == []
    assert await queue.complete_camera(
        first["id"], first["claim_token"], "worker-a", created["id"], created["id"]
    )
    successor = (await queue.claim("worker-b", 1))[0]
    assert successor["id"] == rows[1]["id"]
    assert not await queue.complete(successor["id"], first["claim_token"], "worker-a")


@pytest.mark.asyncio
async def test_expired_lease_can_be_reclaimed_but_active_lease_cannot(repositories):
    database, cameras, _, queue, _ = repositories
    await cameras.create(CameraCreate(name="Gate", stream_url="0", stream_type="webcam"), 2)
    first = (await queue.claim("worker-a", 1))[0]
    assert await queue.claim("worker-b", 1) == []
    await database.write(lambda connection: connection.execute(
        "UPDATE sync_queue SET lease_expires_at=? WHERE id=?",
        (iso(datetime.now(UTC) - timedelta(seconds=1)), first["id"]),
    ))
    reclaimed = (await queue.claim("worker-b", 1))[0]
    assert reclaimed["id"] == first["id"]
    assert reclaimed["claim_token"] != first["claim_token"]


@pytest.mark.asyncio
async def test_event_waits_for_camera_and_uses_canonical_central_contract(repositories):
    _, cameras, analytics, queue, _ = repositories
    camera = await cameras.create(CameraCreate(name="Gate", stream_url="0", stream_type="webcam"), 2)
    event = await analytics.add_event(AnalyticsEventCreate(
        camera_id=UUID(camera["id"]), event_type="zone_enter", payload={"track_id": 7}
    ))
    assert event["camera_id"] == camera["id"]
    assert "captured_image_path" not in event
    claimed = await queue.claim("worker", 10)
    assert len(claimed) == 1 and claimed[0]["payload"]["_kind"] == "camera"


@pytest.mark.asyncio
async def test_concurrent_queue_claims_do_not_duplicate_work(repositories):
    _, cameras, _, queue, _ = repositories
    for index in range(8):
        await cameras.create(CameraCreate(name=f"Camera {index}", stream_url=str(index), stream_type="webcam"), 10)
    first, second = await asyncio.gather(queue.claim("worker-a", 8), queue.claim("worker-b", 8))
    ids = [item["id"] for item in first + second]
    assert len(ids) == 8 == len(set(ids))


@pytest.mark.asyncio
async def test_permanent_and_exhausted_failures_dead_letter_with_visibility(repositories):
    _, cameras, _, queue, _ = repositories
    await cameras.create(CameraCreate(name="Gate", stream_url="0", stream_type="webcam"), 2)
    item = (await queue.claim("worker", 1))[0]
    assert await queue.fail(
        item["id"], item["claim_token"], "worker", "contract", "safe message", True
    )
    assert await queue.dead_letter_count() == 1
    listing = await queue.list_dead_letters(10, 0)
    assert listing["total"] == 1 and listing["items"][0]["last_error_code"] == "contract"
    assert await queue.retry_dead_letter(item["id"])
    assert await queue.dead_letter_count() == 0


@pytest.mark.asyncio
async def test_transient_failure_exhaustion_dead_letters_and_can_be_discarded(repositories):
    database, cameras, _, queue, _ = repositories
    await cameras.create(CameraCreate(name="Gate", stream_url="0", stream_type="webcam"), 2)
    item_id = None
    for _ in range(3):
        item = (await queue.claim("worker", 1))[0]
        item_id = item["id"]
        assert await queue.fail(
            item["id"], item["claim_token"], "worker", "offline", "safe", False
        )
        await database.write(lambda connection: connection.execute(
            "UPDATE sync_queue SET next_attempt_at=? WHERE id=?", (iso(), item["id"])
        ))
    assert await queue.dead_letter_count() == 1
    assert await queue.discard_dead_letter(item_id)
    assert await queue.dead_letter_count() == 0


@pytest.mark.asyncio
async def test_session_cache_is_encrypted_versioned_and_revocation_pending(repositories):
    database, _, _, _, sessions = repositories
    expiry = datetime.now(UTC) + timedelta(hours=1)
    session = LocalSession(
        access_token="jwt-sensitive", session_token="session-sensitive",
        refresh_token="refresh-sensitive", access_expires_at=datetime.now(UTC) + timedelta(minutes=15),
        user={"id": "u-1", "email": "u@example.test"},
    )
    record = await sessions.save(session, expiry)
    raw = await database.read(lambda connection: connection.execute(
        "SELECT encrypted_payload FROM local_sessions"
    ).fetchone()[0])
    assert "sensitive" not in raw
    generation = await sessions.mark_revocation_pending(record.generation)
    assert generation == record.generation + 1
    assert (await sessions.get_record()).status == "revocation_pending"
