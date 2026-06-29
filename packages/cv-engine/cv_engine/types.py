"""Strongly typed, backend-neutral data contracts for CV analytics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, TypeAlias

Point: TypeAlias = tuple[float, float]
BBox: TypeAlias = tuple[float, float, float, float]


class ZoneEventType(StrEnum):
    ENTER = "enter"
    EXIT = "exit"


class CountDirection(StrEnum):
    IN = "in"
    OUT = "out"


@dataclass(frozen=True, slots=True)
class Detection:
    bbox: BBox
    confidence: float
    class_id: int
    class_name: str

    @property
    def centroid(self) -> Point:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


@dataclass(frozen=True, slots=True)
class TrackedObject:
    bbox: BBox
    track_id: int
    class_id: int = 0
    class_name: str = "person"
    confidence: float = 1.0

    @property
    def centroid(self) -> Point:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


@dataclass(frozen=True, slots=True)
class FaceMatch:
    bbox: BBox
    name: str
    confidence: float
    person_id: str | None = None


@dataclass(frozen=True, slots=True)
class Zone:
    id: str
    name: str
    vertices: tuple[Point, ...]
    color: tuple[int, int, int] = (0, 255, 0)
    alert_on_entry: bool = False
    normalized: bool = True

    def __post_init__(self) -> None:
        if len(self.vertices) < 3:
            raise ValueError("a zone requires at least three vertices")


@dataclass(frozen=True, slots=True)
class CountingLine:
    start: Point
    end: Point
    normalized: bool = True

    def __post_init__(self) -> None:
        if self.start == self.end:
            raise ValueError("counting line endpoints must differ")


@dataclass(frozen=True, slots=True)
class ZoneEvent:
    zone_id: str
    zone_name: str
    track_id: int
    event_type: ZoneEventType
    timestamp: datetime
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class CountEvent:
    track_id: int
    direction: CountDirection
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class CountUpdate:
    count_in: int
    count_out: int
    events: tuple[CountEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class IntrusionEvent:
    zone_id: str
    zone_name: str
    track_id: int
    timestamp: datetime
    confidence: float


@dataclass(frozen=True, slots=True)
class AnalyticsEvent:
    event_type: str
    timestamp: datetime
    track_id: int | None
    zone_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OverlayData:
    tracks: tuple[TrackedObject, ...]
    zones: tuple[Zone, ...]
    face_matches: tuple[FaceMatch, ...]
    count_in: int
    count_out: int


@dataclass(frozen=True, slots=True)
class AnalyticsResult:
    timestamp: datetime
    detections: tuple[Detection, ...]
    tracked: tuple[TrackedObject, ...]
    face_matches: tuple[FaceMatch, ...]
    zone_events: tuple[ZoneEvent, ...]
    people_count: CountUpdate
    intrusions: tuple[IntrusionEvent, ...]
    overlay_data: OverlayData
    processing_ms: float
