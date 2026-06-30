from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import UTCModel


class AnalyticsEventIn(UTCModel):
    id: UUID
    camera_id: UUID
    event_type: str = Field(min_length=1, max_length=50)
    payload: dict
    captured_image_id: UUID | None = None
    created_at: datetime


class AlertIn(UTCModel):
    id: UUID
    camera_id: UUID
    zone_id: str | None = None
    captured_image_id: UUID | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    resolved: bool = False
    created_at: datetime


class PeopleCountIn(UTCModel):
    id: UUID
    camera_id: UUID
    count_in: int = Field(ge=0)
    count_out: int = Field(ge=0)
    timestamp: datetime


class SyncEventsRequest(UTCModel):
    events: list[AnalyticsEventIn] = Field(min_length=1, max_length=500)


class SyncAlertsRequest(UTCModel):
    alerts: list[AlertIn] = Field(min_length=1, max_length=500)


class SyncPeopleCountsRequest(UTCModel):
    snapshots: list[PeopleCountIn] = Field(min_length=1, max_length=500)


class HeartbeatRequest(UTCModel):
    timestamp: datetime
    camera_statuses: dict[str, Literal["active", "inactive", "error"]] = Field(min_length=1, max_length=500)
