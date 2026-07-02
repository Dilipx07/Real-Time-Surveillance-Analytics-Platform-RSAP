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
