from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.clients import ExternalServiceError
from app.crypto import FieldCipher
from app.database import Database
from app.dtos import CentralAlert, CentralAnalyticsEvent, CentralCameraCreate, CentralPeopleCount, PermanentContractError
from app.repositories import CameraRepository, SessionRepository, SyncQueueRepository, iso
from app.schemas import CameraCreate, LocalSession
from app.services import SyncService


CENTRAL_ROOT = Path(__file__).parents[2] / "webapp-backend"


def validate_with_agent1(model: str, payload: dict) -> None:
    code = (
        "import json,sys; "
        "from app.schemas.analytics import SyncEventsRequest,SyncAlertsRequest,SyncPeopleCountsRequest; "
        "from app.schemas.camera import CameraCreate; "
        f"model={{'event':SyncEventsRequest,'alert':SyncAlertsRequest,'count':SyncPeopleCountsRequest,'camera':CameraCreate}}['{model}']; "
        "model.model_validate(json.load(sys.stdin))"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(CENTRAL_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", code], input=json.dumps(payload), text=True,
        cwd=CENTRAL_ROOT, env=environment, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_analytics_payload_contracts_validate_against_actual_agent1_schemas():
    camera_id, item_id = uuid4(), uuid4()
    event = CentralAnalyticsEvent(
        id=item_id, camera_id=camera_id, event_type="zone_enter", payload={},
        created_at=datetime.now(UTC),
    )
    alert = CentralAlert(
        id=item_id, camera_id=camera_id, confidence=0.9, created_at=datetime.now(UTC),
    )
    count = CentralPeopleCount(
        id=item_id, camera_id=camera_id, count_in=1, count_out=0, timestamp=datetime.now(UTC),
    )
    validate_with_agent1("event", {"events": [event.model_dump(mode="json")]})
    validate_with_agent1("alert", {"alerts": [alert.model_dump(mode="json")]})
    validate_with_agent1("count", {"snapshots": [count.model_dump(mode="json")]})


def test_camera_payload_contract_validates_against_actual_agent1_schema():
    dto = CentralCameraCreate(
        id=uuid4(), name="Gate", stream_url="rtsp://camera/live", stream_type="rtsp"
    )
    validate_with_agent1("camera", dto.model_dump(mode="json"))


def test_local_capture_path_without_central_file_id_fails_permanently():
    from app.dtos import require_central_image_id

    with pytest.raises(PermanentContractError):
        require_central_image_id("C:/capture.jpg", None)


class IdempotentCentral:
    def __init__(self) -> None:
        self.camera_ids: set[str] = set()
        self.lose_first_response = True

    async def request(self, method, path, session, body=None):
        if method == "POST" and path == "/api/v1/cameras/":
            self.camera_ids.add(str(body["id"]))
            if self.lose_first_response:
                self.lose_first_response = False
                raise ExternalServiceError("central_unavailable", "unavailable", 503, True)
            return {"id": body["id"]}
        return {"id": body.get("id") if body else None}

    async def logout(self, session):
        return None


@pytest.mark.asyncio
async def test_camera_idempotency_reconciles_after_lost_response_and_restart(settings):
    database = Database(settings)
    await database.migrate()
    cipher = FieldCipher(settings.field_encryption_key_bytes)
    cameras = CameraRepository(database, cipher, 3)
    sessions = SessionRepository(database, cipher)
    queue = SyncQueueRepository(database, 30, 3)
    expiry = datetime.now(UTC) + timedelta(hours=1)
    session = LocalSession(
        access_token="access", refresh_token="refresh", session_token="session",
        access_expires_at=datetime.now(UTC) + timedelta(minutes=15),
        user={"id": "user", "role": "va_user"},
        license={"valid_until": expiry.isoformat(), "is_active": True, "max_cameras": 2},
    )
    await sessions.save(session, expiry)
    camera = await cameras.create(CameraCreate(name="Gate", stream_url="0", stream_type="webcam"), 2)
    central = IdempotentCentral()
    service = SyncService(queue, sessions, central, 7, 30)
    first = await service.flush_once("worker")
    assert first["failed"] == 1 and len(central.camera_ids) == 1
    await database.write(lambda connection: connection.execute(
        "UPDATE sync_queue SET next_attempt_at=? WHERE state='retry_wait'", (iso(),)
    ))
    restarted = SyncService(queue, sessions, central, 7, 30)
    second = await restarted.flush_once("worker-after-restart")
    assert second["synced"] == 1 and len(central.camera_ids) == 1
    stored = await cameras.get(UUID(camera["id"]))
    assert stored["server_id"] == camera["id"]
    await database.close()
