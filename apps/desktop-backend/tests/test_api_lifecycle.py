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
            user={"id": "user-1", "email": "va@example.test", "role": "va_user", "permissions": []},
            license={
                "valid_until": expiry.isoformat(), "is_active": True, "max_cameras": 2,
                "analytics_modules": ["intrusion_detection", "people_counting", "zone_analytics"],
            },
        )
        client.portal.call(container.sessions.save, session, expiry)

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["data"]["database"] == "ok"

        cors_health = client.options(
            "/health",
            headers={
                "Origin": "http://127.0.0.1:1420",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert cors_health.status_code == 200
        assert cors_health.headers["access-control-allow-origin"] == "http://127.0.0.1:1420"

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
        assert listed.json()["data"]["items"][0]["id"] == camera_id

        invalid = client.post("/cameras", headers=auth_headers(), json={})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "validation_error"

        for response in (client.get("/missing"), client.put("/health")):
            assert response.status_code in {404, 405}
            assert response.json()["success"] is False
            assert response.json()["data"] is None
            assert set(response.json()) == {"success", "data", "error"}

        container.database._closed = True
        failed = client.get("/health")
        assert failed.status_code == 500
        assert failed.json()["error"] == {
            "code": "internal_error", "message": "Internal server error",
        }

    assert container.database._closed is True
