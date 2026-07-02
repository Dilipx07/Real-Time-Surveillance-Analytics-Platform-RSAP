"""Desktop-local authentication, authorization, and synchronization services."""

from __future__ import annotations

import asyncio
import hmac
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.authorization import AuthorizationService
from app.clients import CentralApiClient, ExternalServiceError
from app.dtos import PermanentContractError
from app.repositories import (
    AnalyticsRepository,
    CameraRepository,
    PersonRepository,
    SessionRepository,
    SyncQueueRepository,
)
from app.schemas import (
    AlertCreate,
    AnalyticsEventCreate,
    CameraCreate,
    CameraUpdate,
    LocalSession,
    PeopleCountCreate,
)


class AuthenticationError(RuntimeError):
    pass


class ConflictError(RuntimeError):
    pass


class AuthService:
    def __init__(
        self,
        sessions: SessionRepository,
        central: CentralApiClient,
        queue: SyncQueueRepository,
    ) -> None:
        self.sessions = sessions
        self.central = central
        self.queue = queue
        self._mutation_lock = asyncio.Lock()

    async def login(self, email: str, password: str) -> LocalSession:
        async with self._mutation_lock:
            session = await self.central.login(email, password)
            try:
                license_data = await self.central.get_user_license(session)
                expiry_value = license_data.get("valid_until")
                if not isinstance(expiry_value, str):
                    raise ExternalServiceError("invalid_response", "Central licence expiry is missing", 502)
                expiry = datetime.fromisoformat(expiry_value.replace("Z", "+00:00")).astimezone(UTC)
                if expiry <= datetime.now(UTC) or not license_data.get("is_active", True):
                    raise AuthenticationError("License is inactive or expired")
                session.license = license_data
                await self.sessions.save(session, expiry)
                return session
            except Exception:
                try:
                    await self.central.logout(session)
                except Exception:
                    pass
                raise

    async def logout(self) -> dict[str, bool]:
        async with self._mutation_lock:
            record = await self.sessions.get_record()
            if record is None:
                return {"logged_out": True, "revocation_pending": False}
            if record.status == "active":
                started = await self.sessions.begin_revocation(
                    record.generation, self.queue.max_attempts, self.queue.lease_seconds
                )
                if started is None:
                    raise ConflictError("Session changed during logout")
                generation, item_id, claim_token = started
            else:
                return {"logged_out": True, "revocation_pending": True}
            try:
                await self.central.logout(record.session)
            except ExternalServiceError as exc:
                if exc.status_code in {401, 403}:
                    completed = await self.queue.complete_revocation(
                        item_id, claim_token, "auth-logout", generation
                    )
                    return {"logged_out": True, "revocation_pending": not completed}
                await self.sessions.set_error(generation, exc.code)
                await self.queue.fail(
                    item_id, claim_token, "auth-logout", exc.code,
                    "Central revocation failed", False,
                )
                return {"logged_out": True, "revocation_pending": True}
            completed = await self.queue.complete_revocation(
                item_id, claim_token, "auth-logout", generation
            )
            return {"logged_out": True, "revocation_pending": not completed}

    async def authenticate(self, bearer_token: str, session_token: str) -> LocalSession:
        record = await self.sessions.get_record()
        if record is None:
            raise AuthenticationError("No local session")
        if record.status != "active":
            raise AuthenticationError("Session revocation is pending")
        if not record.session.license or record.session.license.get("is_active") is False:
            await self.sessions.clear(record.generation)
            raise AuthenticationError("License is inactive")
        if record.license_valid_until is None or record.license_valid_until <= datetime.now(UTC):
            await self.sessions.clear(record.generation)
            raise AuthenticationError("License is expired")
        if record.session.access_expires_at <= datetime.now(UTC):
            raise AuthenticationError("Access token is expired")
        if not hmac.compare_digest(record.session.access_token, bearer_token) or not hmac.compare_digest(
            record.session.session_token, session_token
        ):
            raise AuthenticationError("Local session token mismatch")
        return record.session

    async def refresh(self, refresh_token: str, session_token: str) -> LocalSession:
        async with self._mutation_lock:
            record = await self.sessions.get_record()
            if record is None or record.status != "active":
                raise AuthenticationError("No active local session")
            session = record.session
            if not session.refresh_token or not hmac.compare_digest(session.refresh_token, refresh_token):
                raise AuthenticationError("Refresh token mismatch")
            if not hmac.compare_digest(session.session_token, session_token):
                raise AuthenticationError("Local session token mismatch")
            if record.license_valid_until is None or record.license_valid_until <= datetime.now(UTC):
                await self.sessions.clear(record.generation)
                raise AuthenticationError("License is expired")
            if not session.license or session.license.get("is_active") is False:
                await self.sessions.clear(record.generation)
                raise AuthenticationError("License is inactive")
            try:
                replacement = await self.central.refresh(session)
            except ExternalServiceError as exc:
                if exc.code == "refresh_uncertain" or exc.status_code in {401, 403}:
                    await self.sessions.clear(record.generation)
                raise
            replaced = await self.sessions.replace_active(
                replacement, record.license_valid_until, record.generation
            )
            if not replaced:
                raise ConflictError("Session changed during refresh")
            return replacement

    async def license_status(self) -> dict[str, Any]:
        record = await self.sessions.get_record()
        if record is None:
            return {"valid": False, "authenticated": False, "expires_at": None, "status": "none"}
        valid = (
            record.status == "active"
            and bool(record.session.license)
            and record.session.license.get("is_active") is not False
            and record.license_valid_until is not None
            and record.license_valid_until > datetime.now(UTC)
        )
        return {
            "valid": valid, "authenticated": valid,
            "expires_at": record.license_valid_until, "status": record.status,
            "last_error": record.last_error,
        }


class CameraService:
    def __init__(self, repository: CameraRepository, authorization: AuthorizationService) -> None:
        self.repository = repository
        self.authorization = authorization

    async def create(self, session: LocalSession, payload: CameraCreate) -> dict[str, Any]:
        self.authorization.require(session, "camera.create")
        try:
            return await self.repository.create(payload, self.authorization.max_cameras(session))
        except ValueError as exc:
            if str(exc) == "license camera limit reached":
                raise ConflictError(str(exc)) from exc
            raise

    async def list(self, session: LocalSession, limit: int, offset: int) -> dict[str, Any]:
        self.authorization.require(session, "camera.read")
        return await self.repository.list(limit, offset)

    async def get(self, session: LocalSession, camera_id: UUID) -> dict[str, Any] | None:
        self.authorization.require(session, "camera.read")
        return await self.repository.get(camera_id)

    async def update(
        self, session: LocalSession, camera_id: UUID, payload: CameraUpdate
    ) -> dict[str, Any] | None:
        self.authorization.require(session, "camera.update")
        if payload.analytics_config is not None or payload.zones is not None:
            self.authorization.require_feature(session, "zone_analytics")
        return await self.repository.update(camera_id, payload)

    async def delete(self, session: LocalSession, camera_id: UUID) -> bool:
        self.authorization.require(session, "camera.delete")
        return await self.repository.delete(camera_id)


class AnalyticsService:
    def __init__(self, repository: AnalyticsRepository, authorization: AuthorizationService) -> None:
        self.repository = repository
        self.authorization = authorization

    async def list(self, session: LocalSession, limit: int, offset: int) -> dict[str, Any]:
        self.authorization.require(session, "analytics.read")
        return await self.repository.list_events(limit, offset)

    async def event(self, session: LocalSession, payload: AnalyticsEventCreate) -> dict[str, Any]:
        self.authorization.require(session, "analytics.write")
        self.authorization.require_analytics_module(session, payload.event_type)
        return await self.repository.add_event(payload)

    async def alert(self, session: LocalSession, payload: AlertCreate) -> dict[str, Any]:
        self.authorization.require(session, "analytics.write")
        self.authorization.require_analytics_module(session, "intrusion")
        return await self.repository.add_alert(payload)

    async def people_count(self, session: LocalSession, payload: PeopleCountCreate) -> dict[str, Any]:
        self.authorization.require(session, "analytics.write")
        self.authorization.require_analytics_module(session, "people")
        return await self.repository.add_people_count(payload)


class PersonService:
    def __init__(self, repository: PersonRepository, authorization: AuthorizationService) -> None:
        self.repository = repository
        self.authorization = authorization

    async def list(self, session: LocalSession, limit: int, offset: int) -> dict[str, Any]:
        self.authorization.require(session, "persons.read")
        return await self.repository.list(limit, offset)


class SyncService:
    """One bounded sync pass; scheduling remains Agent-3 ownership."""

    def __init__(
        self,
        queue: SyncQueueRepository,
        sessions: SessionRepository,
        central: CentralApiClient,
        succeeded_retention_days: int,
        dead_letter_retention_days: int,
    ) -> None:
        self.queue = queue
        self.sessions = sessions
        self.central = central
        self.succeeded_retention_days = succeeded_retention_days
        self.dead_letter_retention_days = dead_letter_retention_days

    async def flush_once(self, owner: str, limit: int = 100) -> dict[str, int]:
        record = await self.sessions.get_record()
        if record is None:
            return {"claimed": 0, "synced": 0, "failed": 0, "dead_lettered": 0}
        key_filter = "session:revoke" if record.status == "revocation_pending" else None
        items = await self.queue.claim(owner, limit, key_filter)
        synced = failed = dead = 0
        for item in items:
            payload = item["payload"]
            try:
                if payload.get("_kind") == "session_revoke":
                    try:
                        await self.central.logout(record.session)
                    except ExternalServiceError as exc:
                        if exc.status_code not in {401, 403}:
                            raise
                    completed = await self.queue.complete_revocation(
                        item["id"], item["claim_token"], item["lease_owner"],
                        int(payload["generation"]),
                    )
                else:
                    method = payload.get("_method", "POST")
                    body = payload.get("body", payload)
                    data: Any = {}
                    if method != "PATCH" or body:
                        data = await self.central.request(method, item["endpoint"], record.session, body)
                    analytics = payload.get("analytics")
                    if analytics:
                        await self.central.request(
                            "PATCH", f"{item['endpoint']}/analytics-config", record.session, analytics
                        )
                    if payload.get("_kind") == "camera" and method != "DELETE":
                        local_id = str(payload["_local_id"])
                        server_id = str(data.get("id", local_id)) if isinstance(data, dict) else local_id
                        if server_id != local_id:
                            raise PermanentContractError("central camera identity differs from canonical UUID")
                        completed = await self.queue.complete_camera(
                            item["id"], item["claim_token"], item["lease_owner"], local_id, server_id
                        )
                    else:
                        completed = await self.queue.complete(
                            item["id"], item["claim_token"], item["lease_owner"]
                        )
                if completed:
                    synced += 1
            except PermanentContractError as exc:
                if await self.queue.fail(
                    item["id"], item["claim_token"], item["lease_owner"],
                    "contract_error", str(exc), True,
                ):
                    dead += 1
            except ExternalServiceError as exc:
                permanent = not exc.retryable and 400 <= exc.status_code < 500
                if await self.queue.fail(
                    item["id"], item["claim_token"], item["lease_owner"],
                    exc.code, "Central request failed", permanent,
                ):
                    failed += 1
                    if permanent or item["attempt_count"] >= self.queue.max_attempts:
                        dead += 1
        await self.queue.purge_retained(
            self.succeeded_retention_days, self.dead_letter_retention_days
        )
        return {"claimed": len(items), "synced": synced, "failed": failed, "dead_lettered": dead}
