"""Validated mappings from local records to central API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from app.schemas import StrictModel, aware_utc


class PermanentContractError(ValueError):
    """A local record cannot satisfy the central contract without operator action."""


class CentralCameraCreate(StrictModel):
    id: UUID
    name: str = Field(min_length=1, max_length=255)
    stream_url: str = Field(min_length=1, max_length=4096)
    stream_type: Literal["rtsp", "webcam", "nvr"]
    location_label: str | None = Field(default=None, max_length=255)
    analytics_config: dict[str, Any] = Field(default_factory=dict)
    zones: list[dict[str, Any]] = Field(default_factory=list)


class CentralCameraUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    stream_url: str | None = Field(default=None, min_length=1, max_length=4096)
    stream_type: Literal["rtsp", "webcam", "nvr"] | None = None
    location_label: str | None = None
    is_active: bool | None = None


class CentralAnalyticsConfig(StrictModel):
    analytics_config: dict[str, Any]
    zones: list[dict[str, Any]]


class CentralAnalyticsEvent(StrictModel):
    id: UUID
    camera_id: UUID
    event_type: str = Field(min_length=1, max_length=50)
    payload: dict[str, Any]
    captured_image_id: UUID | None = None
    created_at: datetime

    @model_validator(mode="after")
    def normalize(self) -> "CentralAnalyticsEvent":
        self.created_at = aware_utc(self.created_at)
        return self


class CentralAlert(StrictModel):
    id: UUID
    camera_id: UUID
    zone_id: str | None = None
    captured_image_id: UUID | None = None
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    resolved: bool = False
    created_at: datetime

    @model_validator(mode="after")
    def normalize(self) -> "CentralAlert":
        self.created_at = aware_utc(self.created_at)
        return self


class CentralPeopleCount(StrictModel):
    id: UUID
    camera_id: UUID
    count_in: int = Field(ge=0)
    count_out: int = Field(ge=0)
    timestamp: datetime

    @model_validator(mode="after")
    def normalize(self) -> "CentralPeopleCount":
        self.timestamp = aware_utc(self.timestamp)
        return self


class CentralPerson(StrictModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: UUID
    name: str
    phone: str


def require_central_image_id(local_path: str | None, image_id: UUID | None) -> UUID | None:
    if local_path and image_id is None:
        raise PermanentContractError("capture file has no central file identifier")
    return image_id
