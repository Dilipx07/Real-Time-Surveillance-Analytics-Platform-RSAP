"""Bounded external service clients used by desktop services."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.schemas import LocalSession


class ExternalServiceError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int = 503, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class CentralApiClient:
    """Typed boundary for the central FastAPI contract."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        timeout = httpx.Timeout(
            settings.request_timeout_seconds,
            connect=settings.connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=settings.central_api_url,
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": "rsap-desktop-backend/1.0"},
        )
        self._attempts = settings.retry_attempts
        self._base_delay = settings.retry_base_delay_seconds

    @staticmethod
    def _headers(session: LocalSession) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {session.access_token}",
            "X-Session-Token": session.session_token,
        }

    @staticmethod
    def _unwrap(response: httpx.Response) -> Any:
        try:
            body = response.json()
        except ValueError as exc:
            raise ExternalServiceError(
                "invalid_response", "Central service returned an invalid response", 502, response.status_code >= 500
            ) from exc
        if response.is_error or not isinstance(body, dict) or body.get("success") is not True:
            message = body.get("error") if isinstance(body, dict) else None
            if not isinstance(message, str) or not message:
                message = "Central service rejected the request"
            raise ExternalServiceError(
                "central_rejected", message, response.status_code, response.status_code >= 500
            )
        return body.get("data")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        session: LocalSession | None = None,
        json: Any = None,
        retry: bool = False,
    ) -> Any:
        attempts = self._attempts if retry else 1
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    method, path, json=json,
                    headers=self._headers(session) if session else None,
                )
                return self._unwrap(response)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt + 1 >= attempts:
                    raise ExternalServiceError(
                        "central_unavailable", "Central service is unavailable", 503, True
                    ) from exc
            except ExternalServiceError as exc:
                if not exc.retryable or attempt + 1 >= attempts:
                    raise
            await asyncio.sleep(self._base_delay * (2**attempt))
        raise AssertionError("unreachable")

    async def login(self, email: str, password: str, device_fingerprint: str | None = None) -> LocalSession:
        data = await self._request("POST", "/api/v1/auth/login", json={
            "email": email, "password": password, "device_fingerprint": device_fingerprint,
        })
        if not isinstance(data, dict):
            raise ExternalServiceError("invalid_response", "Central login response is invalid", 502)
        expires_in = data.get("expires_in")
        if not isinstance(expires_in, int) or expires_in <= 0:
            raise ExternalServiceError("invalid_response", "Central token lifetime is invalid", 502)
        data["access_expires_at"] = datetime.now(UTC) + timedelta(seconds=expires_in)
        return LocalSession.model_validate(data)

    async def refresh(self, session: LocalSession) -> LocalSession:
        if not session.refresh_token:
            raise ExternalServiceError("refresh_unavailable", "No refresh token is cached", 401)
        try:
            response = await self._client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": session.refresh_token},
                headers={
                    "Authorization": f"Bearer {session.refresh_token}",
                    "X-Session-Token": session.session_token,
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise ExternalServiceError(
                "refresh_uncertain", "Token refresh outcome is uncertain; sign in again", 503
            ) from exc
        data = self._unwrap(response)
        if not isinstance(data, dict):
            raise ExternalServiceError("invalid_response", "Central refresh response is invalid", 502)
        expires_in = data.get("expires_in")
        if not isinstance(expires_in, int) or expires_in <= 0:
            raise ExternalServiceError("invalid_response", "Central token lifetime is invalid", 502)
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        if not isinstance(access_token, str) or not access_token or not isinstance(refresh_token, str) or not refresh_token:
            raise ExternalServiceError("invalid_response", "Central refresh tokens are invalid", 502)
        return session.model_copy(update={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "access_expires_at": datetime.now(UTC) + timedelta(seconds=expires_in),
        })

    async def logout(self, session: LocalSession) -> None:
        await self._request("POST", "/api/v1/auth/logout", session=session)

    async def get_user_license(self, session: LocalSession) -> dict[str, Any]:
        user_id = session.user.get("id")
        data = await self._request(
            "GET", f"/api/v1/licenses/user/{user_id}", session=session, retry=True
        )
        if not isinstance(data, dict):
            raise ExternalServiceError("invalid_response", "Central licence response is invalid", 502)
        return data

    async def request(
        self, method: str, path: str, session: LocalSession, body: dict[str, Any] | None = None
    ) -> Any:
        safe_to_retry = method.upper() in {"GET", "PUT", "PATCH", "DELETE"} or path.startswith(
            "/api/v1/sync/"
        )
        return await self._request(method, path, session=session, json=body, retry=safe_to_retry)

    async def close(self) -> None:
        await self._client.aclose()


class FileServerClient:
    """Explicit private file-service boundary; no MinIO implementation leakage."""

    def __init__(
        self, base_url: str, service_token: str, timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not service_token:
            raise ValueError("file service token is required")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport,
            headers={"X-Service-Token": service_token}, follow_redirects=False,
        )

    async def upload_capture(self, content: bytes, content_type: str) -> dict[str, Any]:
        response = await self._client.post(
            "/upload/capture", files={"file": ("capture", content, content_type)}
        )
        if response.is_error:
            raise ExternalServiceError("file_upload_failed", "File service rejected capture", response.status_code)
        return response.json()

    async def close(self) -> None:
        await self._client.aclose()
