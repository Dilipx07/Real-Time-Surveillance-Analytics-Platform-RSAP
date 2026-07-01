"""Desktop-local authentication and offline synchronization services."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Any

from app.clients import CentralApiClient, ExternalServiceError
from app.repositories import CameraRepository, SessionRepository, SyncQueueRepository
from app.schemas import LocalSession


class AuthenticationError(RuntimeError):
    pass


class AuthService:
    def __init__(self, sessions: SessionRepository, central: CentralApiClient) -> None:
        self.sessions = sessions
        self.central = central

    async def login(self, email: str, password: str) -> LocalSession:
        session = await self.central.login(email, password)
        try:
            license_data = await self.central.get_user_license(session)
        except Exception:
            try:
                await self.central.logout(session)
            except Exception:
                pass
            raise
        expiry_value = license_data.get("valid_until")
        if not isinstance(expiry_value, str):
            raise ExternalServiceError("invalid_response", "Central licence expiry is missing", 502)
        expiry = datetime.fromisoformat(expiry_value.replace("Z", "+00:00")).astimezone(UTC)
        if expiry <= datetime.now(UTC) or not license_data.get("is_active", True):
            try:
                await self.central.logout(session)
            finally:
                raise AuthenticationError("License is inactive or expired")
        session.license = license_data
        await self.sessions.save(session, expiry)
        return session

    async def logout(self) -> None:
        cached = await self.sessions.get()
        try:
            if cached:
                await self.central.logout(cached[0])
        finally:
            await self.sessions.clear()

    async def authenticate(self, bearer_token: str, session_token: str) -> LocalSession:
        cached = await self.sessions.get()
        if cached is None:
            raise AuthenticationError("No local session")
        session, expiry = cached
        if expiry is None or expiry <= datetime.now(UTC):
            await self.sessions.clear()
            raise AuthenticationError("License is expired")
        if session.access_expires_at <= datetime.now(UTC):
            raise AuthenticationError("Access token is expired")
        if not hmac.compare_digest(session.access_token, bearer_token) or not hmac.compare_digest(
            session.session_token, session_token
        ):
            raise AuthenticationError("Local session token mismatch")
        return session

    async def refresh(self, refresh_token: str, session_token: str) -> LocalSession:
        cached = await self.sessions.get()
        if cached is None:
            raise AuthenticationError("No local session")
        session, license_expiry = cached
        if not session.refresh_token or not hmac.compare_digest(session.refresh_token, refresh_token):
            raise AuthenticationError("Refresh token mismatch")
        if not hmac.compare_digest(session.session_token, session_token):
            raise AuthenticationError("Local session token mismatch")
        if license_expiry is None or license_expiry <= datetime.now(UTC):
            await self.sessions.clear()
            raise AuthenticationError("License is expired")
        try:
            replacement = await self.central.refresh(session)
        except ExternalServiceError as exc:
            if exc.code == "refresh_uncertain":
                await self.sessions.clear()
            raise
        await self.sessions.save(replacement, license_expiry)
        return replacement

    async def license_status(self) -> dict[str, Any]:
        cached = await self.sessions.get()
        if cached is None:
            return {"valid": False, "authenticated": False, "expires_at": None}
        _, expiry = cached
        valid = expiry is not None and expiry > datetime.now(UTC)
        return {"valid": valid, "authenticated": valid, "expires_at": expiry}


class SyncService:
    """One bounded sync pass; scheduling remains Agent-3 ownership."""

    def __init__(
        self,
        queue: SyncQueueRepository,
        sessions: SessionRepository,
        cameras: CameraRepository,
        central: CentralApiClient,
    ) -> None:
        self.queue = queue
        self.sessions = sessions
        self.cameras = cameras
        self.central = central

    async def flush_once(self, owner: str, limit: int = 100) -> dict[str, int]:
        cached = await self.sessions.get()
        if cached is None:
            return {"claimed": 0, "synced": 0, "failed": 0}
        session, _ = cached
        items = await self.queue.claim(owner, limit)
        synced = failed = 0
        for item in items:
            try:
                payload = item["payload"]
                method = payload.get("_method", "POST")
                local_id = payload.get("_local_id")
                body = payload.get("body", payload)
                analytics = body.pop("_analytics", None) if isinstance(body, dict) else None
                data: Any = {}
                if method != "PATCH" or body:
                    data = await self.central.request(method, item["endpoint"], session, body)
                if analytics:
                    await self.central.request(
                        "PATCH", f"{item['endpoint']}/analytics-config", session, analytics
                    )
                if local_id and method != "DELETE":
                    server_id = data.get("id") if method == "POST" and isinstance(data, dict) else None
                    await self.cameras.mark_synced(local_id, str(server_id) if server_id else None)
                if await self.queue.complete(item["id"], item["claim_token"]):
                    synced += 1
            except ExternalServiceError as exc:
                if await self.queue.fail(item["id"], item["claim_token"], exc.code):
                    failed += 1
        return {"claimed": len(items), "synced": synced, "failed": failed}
