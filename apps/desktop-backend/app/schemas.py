"""Typed local API and persistence contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(UTC)


class LoginRequest(StrictModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class LocalSession(StrictModel):
    access_token: str = Field(min_length=1, max_length=8192)
    session_token: str = Field(min_length=1, max_length=4096)
    refresh_token: str | None = Field(default=None, min_length=1, max_length=8192)
    token_type: Literal["bearer"] = "bearer"
    access_expires_at: datetime
    user: dict[str, Any]
    license: dict[str, Any] | None = None

    _normalize_access_expiry = field_validator("access_expires_at")(aware_utc)


class SessionRecord(BaseModel):
    session: LocalSession
    license_valid_until: datetime | None
    generation: int = Field(ge=0)
    status: Literal["active", "revocation_pending"]
    last_error: str | None = None


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=1, max_length=8192)


class CameraCreate(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    stream_url: str = Field(min_length=1, max_length=4096)
    stream_type: Literal["rtsp", "webcam", "nvr"]
    location_label: str | None = Field(default=None, max_length=255)
    analytics_config: dict[str, Any] = Field(default_factory=dict)
    zones: list[dict[str, Any]] = Field(default_factory=list)


class CameraUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    stream_url: str | None = Field(default=None, min_length=1, max_length=4096)
    stream_type: Literal["rtsp", "webcam", "nvr"] | None = None
    location_label: str | None = Field(default=None, max_length=255)
    analytics_config: dict[str, Any] | None = None
    zones: list[dict[str, Any]] | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_partial_update(self) -> "CameraUpdate":
        supplied = self.model_fields_set
        if not supplied:
            raise ValueError("at least one field is required")
        non_nullable = {"name", "stream_url", "stream_type", "analytics_config", "zones", "is_active"}
        if any(getattr(self, field) is None for field in supplied & non_nullable):
            raise ValueError("camera update field cannot be null")
        return self


class AnalyticsEventCreate(StrictModel):
    id: UUID | None = None
    camera_id: UUID
    event_type: str = Field(min_length=1, max_length=50)
    payload: dict[str, Any]
    captured_image_path: str | None = Field(default=None, max_length=4096)
    captured_image_id: UUID | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _normalize_created = field_validator("created_at")(aware_utc)


class AlertCreate(StrictModel):
    id: UUID | None = None
    camera_id: UUID
    zone_id: str | None = Field(default=None, max_length=255)
    image_path: str | None = Field(default=None, max_length=4096)
    captured_image_id: UUID | None = None
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    resolved: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _normalize_created = field_validator("created_at")(aware_utc)


class PeopleCountCreate(StrictModel):
    id: UUID | None = None
    camera_id: UUID
    count_in: int = Field(ge=0, le=2_147_483_647)
    count_out: int = Field(ge=0, le=2_147_483_647)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    _normalize_captured = field_validator("captured_at")(aware_utc)


class PersonCacheEntry(StrictModel):
    server_id: UUID
    name: str = Field(min_length=1, max_length=255)
    phone: str = Field(max_length=32)
    face_encoding_path: str | None = Field(default=None, max_length=4096)
    synced_at: datetime

    _normalize_synced = field_validator("synced_at")(aware_utc)


class RuntimeStatus(BaseModel):
    connected: bool
    queue_count: int
    dead_letter_count: int
    last_checked_at: datetime | None = None
    last_error: str | None = None


class FileUploadResult(StrictModel):
    file_id: UUID
    bucket: str = Field(min_length=1, max_length=100)
    object_name: str = Field(min_length=1, max_length=1024)
    content_type: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0)
    url: HttpUrl | None = None
