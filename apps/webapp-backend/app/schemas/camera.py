from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import StrictModel


class CameraCreate(StrictModel):
    id: UUID
    name: str = Field(min_length=1, max_length=255)
    stream_url: str = Field(min_length=1, max_length=4096)
    stream_type: Literal["rtsp", "webcam", "nvr"]
    location_label: str | None = Field(default=None, max_length=255)
    analytics_config: dict = Field(default_factory=dict)
    zones: list[dict] = Field(default_factory=list)


class CameraUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    stream_url: str | None = Field(default=None, min_length=1, max_length=4096)
    stream_type: Literal["rtsp", "webcam", "nvr"] | None = None
    location_label: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None

    @field_validator("name", "stream_url", "stream_type", "is_active")
    @classmethod
    def reject_null_required_fields(cls, value):
        if value is None:
            raise ValueError("field cannot be null")
        return value


class AnalyticsConfigUpdate(StrictModel):
    analytics_config: dict
    zones: list[dict] = Field(default_factory=list)
