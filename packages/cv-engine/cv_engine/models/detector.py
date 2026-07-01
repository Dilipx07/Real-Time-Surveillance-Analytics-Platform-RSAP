"""Ultralytics detector adapter with lazy imports and shared model instances."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from ..device import DeviceSelection, select_device
from ..types import Detection


class DetectorUnavailableError(RuntimeError):
    """Raised when the optional Ultralytics runtime is unavailable."""


@dataclass(slots=True)
class CachedModel:
    """One shared model plus the lock that serializes its inference calls."""

    model: Any
    inference_lock: threading.Lock


class YOLODetector:
    """Load a YOLO model once per path/device and expose typed detections."""

    _models: ClassVar[dict[tuple[str, str], CachedModel]] = {}
    _model_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(
        self,
        model_path: str | Path,
        *,
        device: str = "auto",
        confidence_threshold: float = 0.5,
        iou_threshold: float = 0.45,
        target_class_ids: tuple[int, ...] = (0, 1, 2, 3),
        model_factory: Callable[[str], Any] | None = None,
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0 or not 0.0 <= iou_threshold <= 1.0:
            raise ValueError("confidence and IoU thresholds must be between 0 and 1")
        if any(class_id < 0 for class_id in target_class_ids):
            raise ValueError("target class IDs must not be negative")
        self.model_path = Path(model_path)
        self.device_selection: DeviceSelection = select_device(device)
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self.target_class_ids = target_class_ids
        self._model_factory = model_factory

    @property
    def model(self) -> Any:
        return self._cached_model.model

    @property
    def _cached_model(self) -> CachedModel:
        key = (str(self.model_path), self.device_selection.resolved)
        with self._model_lock:
            if key not in self._models:
                factory = self._model_factory or self._default_factory
                self._models[key] = CachedModel(factory(str(self.model_path)), threading.Lock())
            return self._models[key]

    @staticmethod
    def _default_factory(model_path: str) -> Any:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise DetectorUnavailableError(
                "Ultralytics is required for YOLO inference; install rsap-cv-engine[yolo]"
            ) from exc
        return YOLO(model_path)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        if not isinstance(frame, np.ndarray) or frame.size == 0 or frame.ndim not in (2, 3):
            raise ValueError("frame must be a non-empty 2D or 3D image array")
        if frame.ndim == 3 and frame.shape[2] not in (1, 3, 4):
            raise ValueError("frame must have 1, 3, or 4 channels")
        height, width = frame.shape[:2]
        cached = self._cached_model
        with cached.inference_lock:
            results = cached.model.predict(
                source=frame,
                conf=self.confidence_threshold,
                iou=self.iou_threshold,
                classes=list(self.target_class_ids),
                device=self.device_selection.resolved,
                verbose=False,
            )
        detections: list[Detection] = []
        for result in results:
            names = result.names
            boxes = result.boxes
            if boxes is None:
                continue
            xyxy = boxes.xyxy.detach().cpu().numpy()
            confidences = boxes.conf.detach().cpu().numpy()
            class_ids = boxes.cls.detach().cpu().numpy().astype(int)
            for bbox, confidence, class_id in zip(xyxy, confidences, class_ids, strict=True):
                if int(class_id) not in self.target_class_ids:
                    continue
                raw = np.asarray(bbox, dtype=float)
                if raw.shape != (4,) or not np.all(np.isfinite(raw)):
                    continue
                clipped = (
                    float(np.clip(raw[0], 0, width)),
                    float(np.clip(raw[1], 0, height)),
                    float(np.clip(raw[2], 0, width)),
                    float(np.clip(raw[3], 0, height)),
                )
                if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
                    continue
                if not np.isfinite(confidence) or not 0.0 <= float(confidence) <= 1.0:
                    continue
                name = names[class_id] if isinstance(names, dict) else names[class_id]
                detections.append(
                    Detection(
                        bbox=clipped,
                        confidence=float(confidence),
                        class_id=int(class_id),
                        class_name=str(name),
                    )
                )
        return detections
