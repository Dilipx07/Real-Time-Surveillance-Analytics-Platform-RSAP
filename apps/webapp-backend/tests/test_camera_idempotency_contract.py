from pathlib import Path
from uuid import uuid4

from app.schemas.camera import CameraCreate
from app.encryption import encrypt_text
from app.routers.cameras import camera_create_matches


def test_camera_create_requires_client_owned_uuid():
    camera_id = uuid4()
    payload = CameraCreate(
        id=camera_id,
        name="Gate",
        stream_url="rtsp://camera/live",
        stream_type="rtsp",
    )
    assert payload.id == camera_id


def test_camera_create_uses_database_backed_idempotency():
    source = (Path(__file__).parents[1] / "app" / "routers" / "cameras.py").read_text()
    assert "INSERT INTO va.cameras(id, user_id" in source
    assert "ON CONFLICT (id) DO NOTHING" in source
    assert "SELECT id FROM auth.users WHERE id=$1 FOR UPDATE" in source
    database_schema = (Path(__file__).parents[3] / "infra" / "postgres" / "init.sql").read_text()
    assert "CREATE TABLE va.cameras (\n    id UUID PRIMARY KEY" in database_schema


def test_camera_retry_requires_an_identical_payload():
    camera_id = uuid4()
    payload = CameraCreate(
        id=camera_id,
        name="Gate",
        stream_url="rtsp://camera/live",
        stream_type="rtsp",
        analytics_config={"people_counting": True},
        zones=[{"id": "entrance"}],
    )
    row = {
        "name": payload.name,
        "stream_url_encrypted": encrypt_text(payload.stream_url),
        "stream_type": payload.stream_type,
        "location_label": payload.location_label,
        "analytics_config": payload.analytics_config,
        "zones": payload.zones,
    }
    assert camera_create_matches(row, payload)
    assert not camera_create_matches(row, payload.model_copy(update={"name": "Conflicting"}))
