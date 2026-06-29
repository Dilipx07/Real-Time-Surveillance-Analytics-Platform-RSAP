"""Configuration objects for one camera analytics pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

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
    tracker_max_age: int = 3
    tracker_min_hits: int = 2
    tracker_iou_threshold: float = 0.3
    executor_workers: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_path", Path(self.model_path))
        if self.device not in {"auto", "cpu", "cuda", "mps"} and not self.device.startswith("cuda:"):
            raise ValueError("device must be auto, cpu, cuda, cuda:N, or mps")
        for name, value in (("confidence_threshold", self.confidence_threshold), ("iou_threshold", self.iou_threshold), ("tracker_iou_threshold", self.tracker_iou_threshold)):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.analytics_fps <= 0 or self.face_interval < 1 or self.executor_workers < 1:
            raise ValueError("fps, face interval, and executor worker count must be positive")
        if self.tracker_max_age < 0 or self.tracker_min_hits < 1:
            raise ValueError("invalid tracker age/hit configuration")
        zone_ids = {zone.id for zone in self.zones}
        missing = self.intrusion_zone_ids - zone_ids
        if missing:
            raise ValueError(f"intrusion zones are not configured: {sorted(missing)}")
