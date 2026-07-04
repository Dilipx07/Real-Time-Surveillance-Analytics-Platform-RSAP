from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from app.clients import CentralApiClient, ExternalServiceError, FileServerClient
from app.crypto import FieldCipher
from app.database import Database
from app.repositories import SessionRepository, SyncQueueRepository
from app.schemas import LocalSession
from app.repositories import iso
from app.services import AuthService, AuthenticationError, SyncService


def response(data, status=200):
    return httpx.Response(status, json={
        "success": status < 400, "data": data if status < 400 else None,
        "error": None if status < 400 else data,
    })


async def auth_parts(settings, handler):
    database = Database(settings)
    await database.migrate()
    sessions = SessionRepository(database, FieldCipher(settings.field_encryption_key_bytes))
    queue = SyncQueueRepository(database, 30, 3)
    central = CentralApiClient(settings, httpx.MockTransport(handler))
    return database, sessions, queue, central, AuthService(sessions, central, queue)


def local_session(expired: bool = False) -> LocalSession:
    return LocalSession(
        access_token="old-access", refresh_token="old-refresh", session_token="session",
        access_expires_at=datetime.now(UTC) + timedelta(seconds=-1 if expired else 900),
        user={"id": "user-1", "role": "va_user", "permissions": []},
        license={
            "valid_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "is_active": True, "max_cameras": 2, "analytics_modules": [],
        },
    )


@pytest.mark.asyncio
async def test_login_fetches_license_and_caches_encrypted_session(settings):
    expiry = (datetime.now(UTC) + timedelta(days=1)).isoformat()

    async def handler(request: httpx.Request):
        if request.url.path.endswith("/auth/login"):
            return response({
                "access_token": "jwt", "refresh_token": "refresh", "session_token": "session",
                "token_type": "bearer", "expires_in": 900,
                "user": {"id": "user-1", "email": "va@example.test", "role": "va_user"},
            })
        return response({"valid_until": expiry, "is_active": True, "max_cameras": 2})

    database, sessions, _, central, auth = await auth_parts(settings, handler)
    session = await auth.login("va@example.test", "password")
    assert session.license["valid_until"] == expiry
    assert (await sessions.get_record()).session.access_token == "jwt"
    await central.close()
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", ["not-a-date", "2030-01-01T00:00:00"])
async def test_login_rejects_invalid_or_naive_central_licence_expiry(settings, expiry):
    async def handler(request: httpx.Request):
        if request.url.path.endswith("/auth/login"):
            return response({
                "access_token": "jwt", "refresh_token": "refresh",
                "session_token": "session", "token_type": "bearer", "expires_in": 900,
                "user": {"id": "user-1", "role": "va_user"},
            })
        if request.url.path.endswith("/auth/logout"):
            return response({"logged_out": True})
        return response({"valid_until": expiry, "is_active": True, "max_cameras": 2})

    database, _, _, central, auth = await auth_parts(settings, handler)
    with pytest.raises(ExternalServiceError) as raised:
        await auth.login("va@example.test", "password")
    assert raised.value.code == "invalid_response"
    await central.close()
    await database.close()


@pytest.mark.asyncio
async def test_bounded_retries_stop_after_configured_attempts(settings):
    calls = 0

    async def handler(_: httpx.Request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline")

    client = CentralApiClient(settings, httpx.MockTransport(handler))
    with pytest.raises(ExternalServiceError) as raised:
        await client.request("POST", "/api/v1/sync/events", local_session(), {"events": []})
    assert raised.value.code == "central_unavailable"
    assert calls == settings.retry_attempts
    await client.close()


@pytest.mark.asyncio
async def test_login_is_not_retried_because_it_rotates_central_session(settings):
    calls = 0

    async def handler(_: httpx.Request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("uncertain outcome")

    client = CentralApiClient(settings, httpx.MockTransport(handler))
    with pytest.raises(ExternalServiceError):
        await client.login("user@example.test", "never-log-this-password")
    assert calls == 1
    await client.close()


@pytest.mark.asyncio
async def test_central_client_rejects_unexpected_content_type(settings):
    client = CentralApiClient(
        settings,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                text='{"success":true,"data":{},"error":null}',
                headers={"content-type": "text/plain"},
            )
        ),
    )
    with pytest.raises(ExternalServiceError) as raised:
        await client.request("GET", "/api/v1/cameras/", local_session())
    assert raised.value.code == "invalid_response"
    await client.close()


@pytest.mark.asyncio
async def test_concurrent_refresh_is_serialized_and_old_token_cannot_replay(settings):
    calls = 0

    async def handler(request: httpx.Request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return response({
            "access_token": "new-access", "refresh_token": "new-refresh",
            "token_type": "bearer", "expires_in": 900,
        })

    database, sessions, _, central, auth = await auth_parts(settings, handler)
    await sessions.save(local_session(), datetime.now(UTC) + timedelta(hours=1))
    results = await asyncio.gather(
        auth.refresh("old-refresh", "session"),
        auth.refresh("old-refresh", "session"),
        return_exceptions=True,
    )
    assert calls == 1
    assert sum(isinstance(result, LocalSession) for result in results) == 1
    assert sum(isinstance(result, AuthenticationError) for result in results) == 1
    await central.close()
    await database.close()


@pytest.mark.asyncio
async def test_logout_timeout_is_durable_denies_access_and_survives_service_restart(settings):
    async def handler(_: httpx.Request):
        raise httpx.ConnectError("offline")

    database, sessions, queue, central, auth = await auth_parts(settings, handler)
    session = local_session()
    await sessions.save(session, datetime.now(UTC) + timedelta(hours=1))
    result = await auth.logout()
    assert result == {"logged_out": True, "revocation_pending": True}
    with pytest.raises(AuthenticationError, match="revocation is pending"):
        await auth.authenticate(session.access_token, session.session_token)
    restarted = AuthService(sessions, central, queue)
    with pytest.raises(AuthenticationError, match="revocation is pending"):
        await restarted.authenticate(session.access_token, session.session_token)
    assert await queue.count() == 1
    await database.write(lambda connection: connection.execute(
        "UPDATE sync_queue SET next_attempt_at=? WHERE logical_key='session:revoke'", (iso(),)
    ))
    success_central = CentralApiClient(
        settings, httpx.MockTransport(lambda _: response({"logged_out": True}))
    )
    sync = SyncService(queue, sessions, success_central, 7, 30)
    assert (await sync.flush_once("restart-worker"))["synced"] == 1
    assert await sessions.get_record() is None
    await success_central.close()
    await central.close()
    await database.close()


@pytest.mark.asyncio
async def test_refresh_racing_logout_cannot_resurrect_session(settings):
    refresh_started = asyncio.Event()
    allow_refresh = asyncio.Event()

    async def handler(request: httpx.Request):
        if request.url.path.endswith("/refresh"):
            refresh_started.set()
            await allow_refresh.wait()
            return response({
                "access_token": "new-access", "refresh_token": "new-refresh",
                "token_type": "bearer", "expires_in": 900,
            })
        return response({"logged_out": True})

    database, sessions, _, central, auth = await auth_parts(settings, handler)
    await sessions.save(local_session(), datetime.now(UTC) + timedelta(hours=1))
    refresh_task = asyncio.create_task(auth.refresh("old-refresh", "session"))
    await refresh_started.wait()
    logout_task = asyncio.create_task(auth.logout())
    allow_refresh.set()
    await refresh_task
    assert (await logout_task)["logged_out"]
    assert await sessions.get_record() is None
    await central.close()
    await database.close()


@pytest.mark.asyncio
async def test_stale_revocation_work_cannot_revoke_a_new_login(settings):
    logout_calls = 0

    async def handler(request: httpx.Request):
        nonlocal logout_calls
        if request.url.path.endswith("/auth/logout"):
            logout_calls += 1
        return response({"logged_out": True})

    database, sessions, queue, central, _ = await auth_parts(settings, handler)
    expiry = datetime.now(UTC) + timedelta(hours=1)
    old = await sessions.save(local_session(), expiry)
    started = await sessions.begin_revocation(old.generation, 3, 30)
    assert started is not None
    replacement = local_session().model_copy(update={
        "access_token": "new-access",
        "refresh_token": "new-refresh",
        "session_token": "new-session",
    })
    current = await sessions.save(replacement, expiry)
    await database.write(lambda connection: connection.execute(
        "UPDATE sync_queue SET lease_expires_at=? WHERE id=?",
        (iso(datetime.now(UTC) - timedelta(seconds=1)), started[1]),
    ))
    sync = SyncService(queue, sessions, central, 7, 30)
    result = await sync.flush_once("recovery-worker")
    assert result["synced"] == 1
    assert logout_calls == 0
    assert (await sessions.get_record()).generation == current.generation
    await central.close()
    await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["json", "missing", "content_type"])
async def test_file_server_response_validation_is_stable(kind):
    def handler(_: httpx.Request):
        if kind == "json":
            return httpx.Response(200, text="not-json", headers={"content-type": "application/json"})
        if kind == "content_type":
            return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})
        return httpx.Response(200, json={"file_id": str(uuid4())})

    client = FileServerClient("http://files.test", "service-token", transport=httpx.MockTransport(handler))
    with pytest.raises(ExternalServiceError) as raised:
        await client.upload_capture(b"jpeg", "image/jpeg")
    assert raised.value.code == "invalid_file_response"
    await client.close()


@pytest.mark.asyncio
async def test_file_server_error_maps_to_stable_error():
    client = FileServerClient(
        "http://files.test", "service-token",
        transport=httpx.MockTransport(lambda _: httpx.Response(503, json={"error": "internal"})),
    )
    with pytest.raises(ExternalServiceError) as raised:
        await client.upload_capture(b"jpeg", "image/jpeg")
    assert raised.value.code == "file_upload_failed"
    assert "internal" not in raised.value.message
    await client.close()
