from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.clients import CentralApiClient, ExternalServiceError
from app.crypto import FieldCipher
from app.database import Database
from app.repositories import SessionRepository
from app.schemas import LocalSession
from app.services import AuthService, AuthenticationError


def response(data, status=200):
    return httpx.Response(status, json={"success": status < 400, "data": data if status < 400 else None,
                                       "error": None if status < 400 else data})


@pytest.mark.asyncio
async def test_login_fetches_license_and_caches_encrypted_session(settings):
    expiry = (datetime.now(UTC) + timedelta(days=1)).isoformat()

    async def handler(request: httpx.Request):
        if request.url.path.endswith("/auth/login"):
            return response({
                "access_token": "jwt", "refresh_token": "refresh", "session_token": "session",
                "token_type": "bearer", "expires_in": 900,
                "user": {"id": "user-1", "email": "va@example.test"},
            })
        return response({"valid_until": expiry, "is_active": True})

    database = Database(settings)
    await database.migrate()
    sessions = SessionRepository(database, FieldCipher(settings.field_encryption_key_bytes))
    central = CentralApiClient(settings, httpx.MockTransport(handler))
    auth = AuthService(sessions, central)
    session = await auth.login("va@example.test", "password")
    assert session.license["valid_until"] == expiry
    assert (await sessions.get())[0].access_token == "jwt"
    await central.close()


@pytest.mark.asyncio
async def test_bounded_retries_stop_after_configured_attempts(settings):
    calls = 0

    async def handler(_: httpx.Request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline")

    client = CentralApiClient(settings, httpx.MockTransport(handler))
    with pytest.raises(ExternalServiceError) as raised:
        await client.request("POST", "/api/v1/sync/events", type("S", (), {
            "access_token": "jwt", "session_token": "session"
        })(), {"events": []})
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
async def test_expired_access_is_rejected_but_refresh_rotates_cache(settings):
    async def handler(request: httpx.Request):
        assert request.headers["X-Session-Token"] == "session"
        assert request.headers["Authorization"] == "Bearer old-refresh"
        return response({
            "access_token": "new-access", "refresh_token": "new-refresh",
            "token_type": "bearer", "expires_in": 900,
        })

    database = Database(settings)
    await database.migrate()
    sessions = SessionRepository(database, FieldCipher(settings.field_encryption_key_bytes))
    central = CentralApiClient(settings, httpx.MockTransport(handler))
    auth = AuthService(sessions, central)
    licence_expiry = datetime.now(UTC) + timedelta(hours=1)
    expired = LocalSession(
        access_token="old-access", refresh_token="old-refresh", session_token="session",
        access_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        user={"id": "user-1"},
    )
    await sessions.save(expired, licence_expiry)
    with pytest.raises(AuthenticationError, match="Access token is expired"):
        await auth.authenticate("old-access", "session")
    replacement = await auth.refresh("old-refresh", "session")
    assert replacement.access_token == "new-access"
    assert (await sessions.get())[0].refresh_token == "new-refresh"
    await central.close()
