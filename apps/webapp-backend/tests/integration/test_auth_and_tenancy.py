import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import os
from uuid import uuid4

import asyncpg
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from starlette.websockets import WebSocketDisconnect


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RSAP_INTEGRATION") != "1", reason="integration services not enabled"),
]


async def reset_and_seed():
    connection = await asyncpg.connect(
        host="127.0.0.1", port=55432, database="rsap_test", user="rsap_test",
        password="integration-postgres-password",
    )
    try:
        await connection.execute(
            """TRUNCATE audit.logs, audit.external_cleanup_outbox, auth.session_outbox,
               va.analytics_events, va.intrusion_alerts, va.cameras, events.registered_persons,
               rbac.permissions, auth.sessions, rbac.licenses, auth.users RESTART IDENTITY CASCADE"""
        )
        from app.security import hash_password

        admin_id = await connection.fetchval(
            """INSERT INTO auth.users(email,password_hash,role)
               VALUES('root@example.com',$1,'super_admin') RETURNING id""",
            hash_password("integration-password"),
        )
        va_id = await connection.fetchval(
            """INSERT INTO auth.users(email,password_hash,role,created_by)
               VALUES('va@example.com',$1,'va_user',$2) RETURNING id""",
            hash_password("integration-password"), admin_id,
        )
        other_id = await connection.fetchval(
            """INSERT INTO auth.users(email,password_hash,role,created_by)
               VALUES('other@example.com',$1,'va_user',$2) RETURNING id""",
            hash_password("integration-password"), admin_id,
        )
        now = datetime.now(UTC)
        for user_id in (admin_id, va_id, other_id):
            await connection.execute(
                """INSERT INTO rbac.licenses(user_id,license_key,valid_from,valid_until,created_by)
                   VALUES($1,$2,$3,$4,$5)""",
                user_id, str(uuid4()), now - timedelta(minutes=1), now + timedelta(hours=1), admin_id,
            )
        for resource in ("sync", "cameras", "analytics"):
            await connection.execute(
                """INSERT INTO rbac.permissions(user_id,resource,actions,granted_by)
                   VALUES($1,$2,ARRAY['*'],$3)""",
                va_id, resource, admin_id,
            )
        va_camera = await connection.fetchval(
            """INSERT INTO va.cameras(user_id,name,stream_url_encrypted,stream_type)
               VALUES($1,'VA camera','unused','rtsp') RETURNING id""",
            va_id,
        )
        other_camera = await connection.fetchval(
            """INSERT INTO va.cameras(user_id,name,stream_url_encrypted,stream_type)
               VALUES($1,'Other camera','unused','rtsp') RETURNING id""",
            other_id,
        )
        return admin_id, va_id, va_camera, other_camera
    finally:
        await connection.close()


def login(client: TestClient, email: str) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "integration-password", "device_fingerprint": "test"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def headers(tokens: dict, access_key: str = "access_token") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {tokens[access_key]}",
        "X-Session-Token": tokens["session_token"],
    }


@pytest.fixture
def seeded():
    values = asyncio.run(reset_and_seed())
    Redis.from_url("redis://:integration-redis-password@127.0.0.1:56379/0").flushdb()
    return values


def test_live_auth_rotation_single_session_ttl_and_logout(seeded):
    from app.config import get_settings
    get_settings.cache_clear()
    from main import app

    with TestClient(app) as client:
        first = login(client, "va@example.com")
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = list(pool.map(lambda _: login(client, "va@example.com"), range(2)))
        redis = Redis.from_url("redis://:integration-redis-password@127.0.0.1:56379/0")
        assert 0 < redis.ttl(f"session:{seeded[1]}") <= 3600
        assert sum(client.get("/api/v1/auth/me", headers=headers(item)).status_code == 200 for item in attempts) == 1
        assert client.get("/api/v1/auth/me", headers=headers(first)).status_code == 401

        current = next(item for item in attempts if client.get("/api/v1/auth/me", headers=headers(item)).status_code == 200)
        refresh_headers = headers(current, "refresh_token")
        rotated_response = client.post(
            "/api/v1/auth/refresh", headers=refresh_headers,
            json={"refresh_token": current["refresh_token"]},
        )
        assert rotated_response.status_code == 200
        rotated = {**rotated_response.json()["data"], "session_token": current["session_token"]}
        replay = client.post(
            "/api/v1/auth/refresh", headers=refresh_headers,
            json={"refresh_token": current["refresh_token"]},
        )
        assert replay.status_code == 401
        assert client.get("/api/v1/auth/me", headers=headers(rotated)).status_code == 401

        active = login(client, "va@example.com")
        assert client.post("/api/v1/auth/logout", headers=headers(active)).status_code == 200
        assert client.get("/api/v1/auth/me", headers=headers(active)).status_code == 401


def test_role_denial_and_cross_tenant_event_alert_conflicts(seeded):
    from app.config import get_settings
    get_settings.cache_clear()
    from main import app

    _, _, va_camera, other_camera = seeded
    event_id, alert_id = uuid4(), uuid4()

    async def seed_conflicts():
        connection = await asyncpg.connect(
            host="127.0.0.1", port=55432, database="rsap_test", user="rsap_test",
            password="integration-postgres-password",
        )
        try:
            await connection.execute(
                "INSERT INTO va.analytics_events(id,camera_id,event_type,payload) VALUES($1,$2,'motion','{}')",
                event_id, other_camera,
            )
            await connection.execute(
                "INSERT INTO va.intrusion_alerts(id,camera_id,resolved) VALUES($1,$2,true)",
                alert_id, other_camera,
            )
        finally:
            await connection.close()

    asyncio.run(seed_conflicts())
    with TestClient(app) as client:
        tokens = login(client, "va@example.com")
        assert client.get("/api/v1/persons/", headers=headers(tokens)).status_code == 403
        now = datetime.now(UTC).isoformat()
        event = client.post(
            "/api/v1/sync/events", headers=headers(tokens),
            json={"events": [{"id": str(event_id), "camera_id": str(va_camera), "event_type": "motion", "payload": {}, "created_at": now}]},
        )
        assert event.status_code == 409
        alert = client.post(
            "/api/v1/sync/alerts", headers=headers(tokens),
            json={"alerts": [{"id": str(alert_id), "camera_id": str(va_camera), "resolved": False, "created_at": now}]},
        )
        assert alert.status_code == 409

        unauthorized_camera = client.post(
            "/api/v1/sync/events", headers=headers(tokens),
            json={"events": [{"id": str(uuid4()), "camera_id": str(other_camera), "event_type": "motion", "payload": {}, "created_at": now}]},
        )
        assert unauthorized_camera.status_code == 403


def test_license_shortening_future_license_force_expiry_and_websocket_revocation(seeded):
    from app.config import get_settings
    get_settings.cache_clear()
    from main import app

    admin_id, va_id, va_camera, _ = seeded

    async def license_ids():
        connection = await asyncpg.connect(
            host="127.0.0.1", port=55432, database="rsap_test", user="rsap_test",
            password="integration-postgres-password",
        )
        try:
            owned = dict(await connection.fetch(
                "SELECT user_id, id FROM rbac.licenses WHERE user_id=ANY($1::uuid[])",
                [admin_id, va_id],
            ))
            future_id = await connection.fetchval(
                """SELECT l.id FROM rbac.licenses l JOIN auth.users u ON u.id=l.user_id
                   WHERE u.email='other@example.com'"""
            )
            return owned, future_id
        finally:
            await connection.close()

    licenses, future_license_id = asyncio.run(license_ids())
    with TestClient(app) as client:
        admin = login(client, "root@example.com")
        va = login(client, "va@example.com")
        shortened_until = datetime.now(UTC) + timedelta(seconds=30)
        shortened = client.patch(
            f"/api/v1/licenses/{licenses[va_id]}", headers=headers(admin),
            json={"valid_until": shortened_until.isoformat()},
        )
        assert shortened.status_code == 200, shortened.text
        redis = Redis.from_url("redis://:integration-redis-password@127.0.0.1:56379/0")
        assert 0 < redis.ttl(f"session:{va_id}") <= 30

        with client.websocket_connect(f"/ws/sync/{va_id}?session_token={va['session_token']}") as socket:
            assert socket.receive_json()["type"] == "config_update"
            redis.delete(f"session:{va_id}", f"refresh:{va_id}")
            socket.send_json({
                "type": "heartbeat",
                "data": {"timestamp": datetime.now(UTC).isoformat(), "camera_statuses": {str(va_camera): "active"}},
            })
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            assert closed.value.code == 1008

        va = login(client, "va@example.com")
        expired = client.delete(
            f"/api/v1/licenses/{licenses[va_id]}/expire", headers=headers(admin)
        )
        assert expired.status_code == 200
        assert client.get("/api/v1/auth/me", headers=headers(va)).status_code == 401

        future_start = datetime.now(UTC) + timedelta(hours=1)
        future = client.patch(
            f"/api/v1/licenses/{future_license_id}", headers=headers(admin),
            json={
                "valid_from": future_start.isoformat(),
                "valid_until": (future_start + timedelta(hours=1)).isoformat(),
            },
        )
        assert future.status_code == 200
        denied = client.post(
            "/api/v1/auth/login",
            json={"email": "other@example.com", "password": "integration-password"},
        )
        assert denied.status_code == 403


def test_stale_desktop_alert_cannot_reopen_resolved_alert(seeded):
    from app.config import get_settings
    get_settings.cache_clear()
    from main import app

    _, _, va_camera, _ = seeded
    alert_id = uuid4()
    with TestClient(app) as client:
        va = login(client, "va@example.com")
        now = datetime.now(UTC).isoformat()
        first = client.post(
            "/api/v1/sync/alerts", headers=headers(va),
            json={"alerts": [{"id": str(alert_id), "camera_id": str(va_camera), "resolved": True, "created_at": now}]},
        )
        assert first.status_code == 200
        retry = client.post(
            "/api/v1/sync/alerts", headers=headers(va),
            json={"alerts": [{"id": str(alert_id), "camera_id": str(va_camera), "resolved": False, "created_at": now}]},
        )
        assert retry.status_code == 200

    async def resolved_value():
        connection = await asyncpg.connect(
            host="127.0.0.1", port=55432, database="rsap_test", user="rsap_test",
            password="integration-postgres-password",
        )
        try:
            return await connection.fetchval(
                "SELECT resolved FROM va.intrusion_alerts WHERE id=$1", alert_id
            )
        finally:
            await connection.close()

    assert asyncio.run(resolved_value()) is True
