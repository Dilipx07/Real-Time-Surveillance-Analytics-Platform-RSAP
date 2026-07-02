from datetime import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.analytics import AnalyticsEventIn
from app.schemas.auth import LoginRequest
from app.schemas.camera import CameraCreate, CameraUpdate
from app.schemas.license import LicenseCreate
from app.schemas.person import PersonUpdate


def test_license_period_must_be_ordered_and_timezone_aware():
    with pytest.raises(ValidationError):
        LicenseCreate(
            user_id=uuid4(), valid_from=datetime(2030, 1, 2), valid_until=datetime(2030, 1, 1),
        )


def test_sync_datetime_must_have_timezone():
    with pytest.raises(ValidationError):
        AnalyticsEventIn(
            id=uuid4(), camera_id=uuid4(), event_type="person", payload={}, created_at=datetime(2030, 1, 1)
        )


def test_camera_and_aadhaar_validation():
    camera = CameraCreate(id=uuid4(), name="Gate", stream_url="rtsp://camera/live", stream_type="rtsp")
    assert camera.stream_type == "rtsp"
    with pytest.raises(ValidationError):
        PersonUpdate(aadhaar_last4="12A4")
    with pytest.raises(ValidationError):
        CameraUpdate(name=None)


def test_documented_local_email_domain_is_accepted():
    login = LoginRequest(email="Admin@RSAP.local", password="password123")
    assert login.email == "admin@rsap.local"
