"""Typed local API and persistence contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class LocalSession(BaseModel):
    access_token: str
    session_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    access_expires_at: datetime
    user: dict[str, Any]
    license: dict[str, Any] | None = None


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


class CameraCreate(StrictModel):
    name: str = Field(min_length=1, max_length=255)
    stream_url: str = Field(min_length=1, max_length=4096)
    stream_type: Literal["rtsp", "webcam", "nvr"]
    location_label: str | None = Field(default=None, max_length=255)
    analytics_config: dict[str, Any] = Field(default_factory=dict)
    zones: list[dict[str, Any]] = Field(default_factory=list)
    is_active: bool = True


class CameraUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    stream_url: str | None = Field(default=None, min_length=1, max_length=4096)
    stream_type: Literal["rtsp", "webcam", "nvr"] | None = None
    location_label: str | None = Field(default=None, max_length=255)
    analytics_config: dict[str, Any] | None = None
    zones: list[dict[str, Any]] | None = None
    is_active: bool | None = None


class AnalyticsEventCreate(StrictModel):
    id: UUID | None = None
    camera_id: UUID
    event_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, Any]
    captured_image_path: str | None = Field(default=None, max_length=4096)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("created_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)


class AlertCreate(StrictModel):
    id: UUID | None = None
    camera_id: UUID
    zone_id: str | None = Field(default=None, max_length=255)
    image_path: str | None = Field(default=None, max_length=4096)
    confidence: float = Field(ge=0, le=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PeopleCountCreate(StrictModel):
    id: UUID | None = None
    camera_id: UUID
    count_in: int = Field(ge=0)
    count_out: int = Field(ge=0)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PersonCacheEntry(StrictModel):
    server_id: UUID
    name: str = Field(min_length=1, max_length=255)
    phone: str = Field(max_length=32)
    face_encoding_path: str | None = Field(default=None, max_length=4096)
    synced_at: datetime


class RuntimeStatus(BaseModel):
    connected: bool
    queue_count: int
    last_checked_at: datetime | None = None
    last_error: str | None = None


class FileUploadResult(BaseModel):
    file_id: UUID
    url: HttpUrl | None = None
    object_name: str
    bucket: str
