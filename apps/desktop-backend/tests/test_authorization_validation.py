from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.authorization import AuthorizationError, AuthorizationService
from app.schemas import AlertCreate, CameraUpdate, LocalSession, PeopleCountCreate


def session(role="va_user", active=True, max_cameras=2):
    return LocalSession(
        access_token="a", refresh_token="r", session_token="s",
        access_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        user={"id": "u", "role": role, "permissions": []},
        license={
            "valid_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "is_active": active, "max_cameras": max_cameras,
            "analytics_modules": ["intrusion_detection", "people_counting"],
        },
    )


def test_authorization_denies_staff_camera_mutation_and_inactive_licence():
    authorization = AuthorizationService()
    with pytest.raises(AuthorizationError):
        authorization.require(session("staff"), "camera.create")
    with pytest.raises(AuthorizationError, match="inactive"):
        authorization.require(session(active=False), "camera.read")


def test_authorization_allows_read_routes_and_exposes_camera_limit():
    authorization = AuthorizationService()
    authorization.require(session(), "camera.read")
    assert authorization.max_cameras(session(max_cameras=3)) == 3


@pytest.mark.parametrize(
    ("role", "permission", "allowed"),
    [
        ("va_user", "camera.read", True),
        ("va_user", "camera.create", True),
        ("va_user", "camera.update", True),
        ("va_user", "camera.delete", True),
        ("va_user", "analytics.read", True),
        ("va_user", "analytics.write", True),
        ("va_user", "persons.read", True),
        ("va_user", "persons.write", False),
        ("va_user", "sync.read", True),
        ("staff", "persons.read", True),
        ("staff", "persons.write", True),
        ("unknown", "camera.read", False),
    ],
)
def test_permission_matrix_is_explicit_and_deny_by_default(role, permission, allowed):
    authorization = AuthorizationService()
    if allowed:
        authorization.require(session(role), permission)
    else:
        with pytest.raises(AuthorizationError):
            authorization.require(session(role), permission)


def test_central_wildcard_grants_are_supported_but_malformed_grants_deny():
    authorization = AuthorizationService()
    granted = session("unknown")
    granted.user["permissions"] = [{"resource": "cameras", "actions": ["*"]}]
    authorization.require(granted, "camera.read")
    denied = session("unknown")
    denied.user["permissions"] = ["malformed"]
    with pytest.raises(AuthorizationError):
        authorization.require(denied, "camera.read")


@pytest.mark.parametrize(
    "license_data",
    [
        {"valid_until": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
        {"valid_until": "2030-01-01T00:00:00", "is_active": True},
    ],
)
def test_incomplete_or_naive_licence_data_fails_closed(license_data):
    candidate = session()
    candidate.license = license_data
    with pytest.raises(AuthorizationError):
        AuthorizationService().require(candidate, "camera.read")


@pytest.mark.parametrize(
    "payload",
    [
        {}, {"name": None}, {"stream_url": None}, {"stream_type": None},
        {"analytics_config": None}, {"zones": None}, {"is_active": None},
    ],
)
def test_camera_update_rejects_empty_and_explicit_null(payload):
    with pytest.raises(ValidationError):
        CameraUpdate.model_validate(payload)


def test_timestamps_and_non_finite_confidence_are_rejected():
    with pytest.raises(ValidationError):
        PeopleCountCreate(camera_id="11111111-1111-4111-8111-111111111111", count_in=1, count_out=2,
                          captured_at=datetime(2030, 1, 1))
    with pytest.raises(ValidationError):
        AlertCreate(camera_id="11111111-1111-4111-8111-111111111111", confidence=float("nan"))
