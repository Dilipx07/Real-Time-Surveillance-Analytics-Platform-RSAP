"""Strongly typed, backend-neutral data contracts for CV analytics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Any, TypeAlias

Point: TypeAlias = tuple[float, float]
BBox: TypeAlias = tuple[float, float, float, float]


def validate_bbox(bbox: object) -> BBox:
    try:
        values = tuple(float(value) for value in bbox)  # type: ignore[union-attr]
    except (TypeError, ValueError) as exc:
        raise ValueError("bbox must contain exactly four numeric coordinates") from exc
    if len(values) != 4:
        raise ValueError("bbox must contain exactly four numeric coordinates")
    if not all(isfinite(value) for value in values):
        raise ValueError("bbox coordinates must be finite")
    if any(value < 0 for value in values):
        raise ValueError("bbox coordinates must not be negative")
    x1, y1, x2, y2 = values
    if x2 <= x1 or y2 <= y1:
        raise ValueError("bbox must have positive width and height")
    return x1, y1, x2, y2


def _validate_score(value: object, name: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise ValueError(f"{name} must be finite and between 0 and 1")
    return numeric


def _orientation(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    epsilon = 1e-12
    ab_c, ab_d = _orientation(a, b, c), _orientation(a, b, d)
    cd_a, cd_b = _orientation(c, d, a), _orientation(c, d, b)
    if (
        ((ab_c > epsilon and ab_d < -epsilon) or (ab_c < -epsilon and ab_d > epsilon))
        and ((cd_a > epsilon and cd_b < -epsilon) or (cd_a < -epsilon and cd_b > epsilon))
    ):
        return True

    def on_segment(start: Point, point: Point, end: Point) -> bool:
        return (
            min(start[0], end[0]) - epsilon <= point[0] <= max(start[0], end[0]) + epsilon
            and min(start[1], end[1]) - epsilon <= point[1] <= max(start[1], end[1]) + epsilon
        )

    return (
        (abs(ab_c) <= epsilon and on_segment(a, c, b))
        or (abs(ab_d) <= epsilon and on_segment(a, d, b))
        or (abs(cd_a) <= epsilon and on_segment(c, a, d))
        or (abs(cd_b) <= epsilon and on_segment(c, b, d))
    )


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "bbox", validate_bbox(self.bbox))
        object.__setattr__(self, "confidence", _validate_score(self.confidence, "detection confidence"))

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

    def __post_init__(self) -> None:
        object.__setattr__(self, "bbox", validate_bbox(self.bbox))
        if self.track_id < 0:
            raise ValueError("track_id must not be negative")
        object.__setattr__(self, "confidence", _validate_score(self.confidence, "track confidence"))

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

    def __post_init__(self) -> None:
        object.__setattr__(self, "bbox", validate_bbox(self.bbox))
        object.__setattr__(self, "confidence", _validate_score(self.confidence, "face similarity"))


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
        vertices = tuple((float(x), float(y)) for x, y in self.vertices)
        if not self.id.strip():
            raise ValueError("zone id must not be empty")
        if not all(isfinite(value) for point in vertices for value in point):
            raise ValueError("zone coordinates must be finite")
        if self.normalized and any(value < 0.0 or value > 1.0 for point in vertices for value in point):
            raise ValueError("normalized zone coordinates must be between 0 and 1")
        if len(set(vertices)) < 3:
            raise ValueError("a zone requires at least three distinct vertices")
        if any(vertices[index] == vertices[(index + 1) % len(vertices)] for index in range(len(vertices))):
            raise ValueError("zone contains duplicate adjacent vertices")
        area = abs(sum(
            vertices[index][0] * vertices[(index + 1) % len(vertices)][1]
            - vertices[(index + 1) % len(vertices)][0] * vertices[index][1]
            for index in range(len(vertices))
        )) / 2.0
        if area <= 1e-12:
            raise ValueError("zone polygon must have nonzero area")
        edge_count = len(vertices)
        for first in range(edge_count):
            for second in range(first + 1, edge_count):
                if second in {first, (first + 1) % edge_count} or first == (second + 1) % edge_count:
                    continue
                if _segments_intersect(
                    vertices[first], vertices[(first + 1) % edge_count],
                    vertices[second], vertices[(second + 1) % edge_count],
                ):
                    raise ValueError("zone polygon must not self-intersect")
        object.__setattr__(self, "vertices", vertices)


@dataclass(frozen=True, slots=True)
class CountingLine:
    start: Point
    end: Point
    normalized: bool = True

    def __post_init__(self) -> None:
        start = tuple(float(value) for value in self.start)
        end = tuple(float(value) for value in self.end)
        if len(start) != 2 or len(end) != 2:
            raise ValueError("counting line endpoints must contain exactly two coordinates")
        if not all(isfinite(value) for value in (*start, *end)):
            raise ValueError("counting line coordinates must be finite")
        if self.normalized and any(value < 0.0 or value > 1.0 for value in (*start, *end)):
            raise ValueError("normalized counting line coordinates must be between 0 and 1")
        if start == end:
            raise ValueError("counting line endpoints must differ")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)


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
