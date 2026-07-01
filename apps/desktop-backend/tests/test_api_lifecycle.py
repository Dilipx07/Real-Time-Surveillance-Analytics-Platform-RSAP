from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.schemas import LocalSession
from main import create_app


def auth_headers():
    return {"Authorization": "Bearer jwt", "X-Session-Token": "session"}


def test_startup_api_auth_envelope_and_shutdown(settings):
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        container = app.state.container
        expiry = datetime.now(UTC) + timedelta(hours=1)
        session = LocalSession(
            access_token="jwt", session_token="session", refresh_token="refresh",
            access_expires_at=datetime.now(UTC) + timedelta(minutes=15),
            user={"id": "user-1", "email": "va@example.test", "role": "va_user"},
        )
        client.portal.call(container.sessions.save, session, expiry)

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["data"]["database"] == "ok"

        denied = client.get("/cameras")
        assert denied.status_code == 401
        assert denied.json() == {
            "success": False, "data": None,
            "error": {"code": "http_401", "message": "Bearer and session tokens are required"},
        }

        created = client.post("/cameras", headers=auth_headers(), json={
            "name": "Front gate", "stream_url": "rtsp://user:secret@camera/live",
            "stream_type": "rtsp",
        })
        assert created.status_code == 201
        camera_id = created.json()["data"]["id"]
        listed = client.get("/cameras", headers=auth_headers())
        assert listed.json()["data"][0]["id"] == camera_id

        invalid = client.post("/cameras", headers=auth_headers(), json={})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "validation_error"

    assert container.database._closed is True
