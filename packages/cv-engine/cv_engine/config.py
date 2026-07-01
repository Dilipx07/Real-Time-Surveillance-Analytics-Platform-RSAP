"""Configuration objects for one camera analytics pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .device import validate_device_syntax
from .types import CountingLine, Zone


@dataclass(frozen=True, slots=True)
class CVConfig:
    model_path: Path = Path("yolov8n.pt")
    device: str = "auto"
    confidence_threshold: float = 0.5
    iou_threshold: float = 0.45
    target_class_ids: tuple[int, ...] = (0, 1, 2, 3)
    analytics_fps: float = 10.0
    face_recognition: bool = False
    face_interval: int = 5
    face_tolerance: float = 0.6
    zones: tuple[Zone, ...] = field(default_factory=tuple)
    counting_line: CountingLine | None = None
    intrusion_zone_ids: frozenset[str] = field(default_factory=frozenset)
    intrusion_cooldown_seconds: float = 5.0
    intrusion_retention_margin_seconds: float = 60.0
    people_counter_stale_after_frames: int = 30
    tracker_max_age: int = 3
    tracker_min_hits: int = 2
    tracker_iou_threshold: float = 0.3
    executor_workers: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_path", Path(self.model_path))
        validate_device_syntax(self.device)
        for name, value in (("confidence_threshold", self.confidence_threshold), ("iou_threshold", self.iou_threshold), ("tracker_iou_threshold", self.tracker_iou_threshold)):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.analytics_fps <= 0 or self.face_interval < 1:
            raise ValueError("fps and face interval must be positive")
        if self.executor_workers != 1:
            raise ValueError("stateful per-camera processing requires executor_workers=1")
        if self.tracker_max_age < 0 or self.tracker_min_hits < 1:
            raise ValueError("invalid tracker age/hit configuration")
        if self.people_counter_stale_after_frames < 0:
            raise ValueError("people counter stale-frame expiry must not be negative")
        if self.intrusion_cooldown_seconds < 0 or self.intrusion_retention_margin_seconds < 0:
            raise ValueError("intrusion cooldown and retention margin must not be negative")
        zone_ids = {zone.id for zone in self.zones}
        if len(zone_ids) != len(self.zones):
            raise ValueError("zone IDs must be unique")
        missing = self.intrusion_zone_ids - zone_ids
        if missing:
            raise ValueError(f"intrusion zones are not configured: {sorted(missing)}")
