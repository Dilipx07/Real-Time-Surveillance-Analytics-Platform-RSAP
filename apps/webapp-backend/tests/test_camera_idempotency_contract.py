from pathlib import Path
from uuid import uuid4

from app.schemas.camera import CameraCreate


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
