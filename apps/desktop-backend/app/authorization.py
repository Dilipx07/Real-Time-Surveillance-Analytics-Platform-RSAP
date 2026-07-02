"""Deny-by-default local role and licence enforcement."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.schemas import LocalSession


class AuthorizationError(PermissionError):
    pass


ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "super_admin": frozenset({"*"}),
    "admin": frozenset({"*"}),
    "va_user": frozenset({
        "camera.read", "camera.create", "camera.update", "camera.delete",
        "analytics.read", "analytics.write", "persons.read", "sync.read",
    }),
    "staff": frozenset({"persons.read", "persons.write"}),
}

RESOURCE_NAMES = {
    "camera": "cameras", "analytics": "analytics", "persons": "persons", "sync": "sync",
}


class AuthorizationService:
    def _license(self, session: LocalSession) -> dict[str, Any]:
        license_data = session.license
        if not isinstance(license_data, dict) or license_data.get("is_active") is False:
            raise AuthorizationError("License is inactive")
        expiry = license_data.get("valid_until")
        if not isinstance(expiry, str):
            raise AuthorizationError("License expiry is unavailable")
        try:
            expires_at = datetime.fromisoformat(expiry.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError as exc:
            raise AuthorizationError("License expiry is invalid") from exc
        if expires_at <= datetime.now(UTC):
            raise AuthorizationError("License is expired")
        return license_data

    def require(self, session: LocalSession, permission: str) -> None:
        self._license(session)
        role = str(session.user.get("role", ""))
        role_permissions = ROLE_PERMISSIONS.get(role, frozenset())
        if "*" in role_permissions or permission in role_permissions:
            return
        resource, action = permission.split(".", 1)
        central_resource = RESOURCE_NAMES.get(resource, resource)
        for grant in session.user.get("permissions", []):
            if grant.get("resource") == central_resource and action in grant.get("actions", []):
                return
        raise AuthorizationError("Insufficient permission")

    def max_cameras(self, session: LocalSession) -> int:
        license_data = self._license(session)
        value = license_data.get("max_cameras")
        if not isinstance(value, int) or value < 1:
            raise AuthorizationError("Camera entitlement is unavailable")
        return value

    def require_analytics_module(self, session: LocalSession, event_type: str) -> None:
        license_data = self._license(session)
        mapping = {
            "intrusion": "intrusion_detection",
            "people": "people_counting",
            "face": "face_recognition",
            "zone": "zone_analytics",
        }
        required = next((module for marker, module in mapping.items() if marker in event_type.lower()), None)
        if required is None:
            return
        modules = set(license_data.get("analytics_modules") or [])
        features = license_data.get("features") or {}
        if required not in modules and features.get(required) is not True:
            raise AuthorizationError(f"Analytics module is not licensed: {required}")

    def require_feature(self, session: LocalSession, feature: str) -> None:
        license_data = self._license(session)
        modules = set(license_data.get("analytics_modules") or [])
        features = license_data.get("features") or {}
        if feature not in modules and features.get(feature) is not True:
            raise AuthorizationError(f"Feature is not licensed: {feature}")
