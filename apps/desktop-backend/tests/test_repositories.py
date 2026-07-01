from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.crypto import FieldCipher
from app.database import Database
from app.repositories import (
    AnalyticsRepository,
    CameraRepository,
    SessionRepository,
    SyncQueueRepository,
)
from app.schemas import AnalyticsEventCreate, CameraCreate, LocalSession
from app.schemas import CameraUpdate


@pytest.fixture
async def repositories(settings):
    database = Database(settings)
    await database.migrate()
    cipher = FieldCipher(settings.field_encryption_key_bytes)
    yield (
        database,
        CameraRepository(database, cipher),
        AnalyticsRepository(database),
        SyncQueueRepository(database, 30),
        SessionRepository(database, cipher),
    )
    await database.close()


@pytest.mark.asyncio
async def test_camera_secret_is_encrypted_and_mutation_queues_atomically(repositories):
    database, cameras, _, queue, _ = repositories
    created = await cameras.create(CameraCreate(
        name="Gate", stream_url="rtsp://operator:secret@camera/live", stream_type="rtsp"
    ))
    assert created["stream_url"].endswith("/live")
    row = await database.read(lambda connection: connection.execute(
        "SELECT stream_url_encrypted FROM local_cameras WHERE id=?", (created["id"],)
    ).fetchone())
    assert "secret" not in row["stream_url_encrypted"]
    assert await queue.count() == 1


@pytest.mark.asyncio
async def test_unsynced_camera_update_coalesces_create_and_delete_cancels_it(repositories):
    database, cameras, _, queue, _ = repositories
    created = await cameras.create(CameraCreate(name="Old", stream_url="0", stream_type="webcam"))
    updated = await cameras.update(created["id"], CameraUpdate(name="New"))
    assert updated["name"] == "New"
    queued = await database.read(lambda connection: connection.execute(
        "SELECT endpoint, payload_json FROM sync_queue"
    ).fetchone())
    assert queued["endpoint"] == "/api/v1/cameras/"
    assert '"_method":"POST"' in queued["payload_json"]
    assert '"name":"New"' in queued["payload_json"]
    assert await cameras.delete(created["id"])
    assert await queue.count() == 0


@pytest.mark.asyncio
async def test_event_and_queue_commit_together(repositories):
    database, cameras, analytics, queue, _ = repositories
    camera = await cameras.create(CameraCreate(name="Gate", stream_url="0", stream_type="webcam"))
    before = await queue.count()
    event = await analytics.add_event(AnalyticsEventCreate(
        camera_id=UUID(camera["id"]), event_type="zone_enter", payload={"track_id": 7}
    ))
    assert event["event_type"] == "zone_enter"
    assert await queue.count() == before + 1
    count = await database.read(lambda connection: connection.execute(
        "SELECT count(*) FROM local_analytics_events"
    ).fetchone()[0])
    assert count == 1


@pytest.mark.asyncio
async def test_concurrent_queue_claims_do_not_duplicate_work(repositories):
    _, cameras, _, queue, _ = repositories
    for index in range(8):
        await cameras.create(CameraCreate(
            name=f"Camera {index}", stream_url=str(index), stream_type="webcam"
        ))
    first, second = await asyncio.gather(queue.claim("worker-a", 8), queue.claim("worker-b", 8))
    ids = [item["id"] for item in first + second]
    assert len(ids) == 8
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_session_cache_is_encrypted_and_expiry_preserved(repositories):
    database, _, _, _, sessions = repositories
    session = LocalSession(
        access_token="jwt-sensitive", session_token="session-sensitive",
        refresh_token="refresh-sensitive", access_expires_at=datetime.now(UTC) + timedelta(minutes=15),
        user={"id": "u-1", "email": "u@example.test"},
    )
    expiry = datetime.now(UTC) + timedelta(hours=1)
    await sessions.save(session, expiry)
    raw = await database.read(lambda connection: connection.execute(
        "SELECT encrypted_payload FROM local_sessions"
    ).fetchone()[0])
    assert "sensitive" not in raw
    loaded, loaded_expiry = await sessions.get()
    assert loaded.session_token == session.session_token
    assert loaded_expiry == expiry
