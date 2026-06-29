"""Ultralytics detector adapter with lazy imports and shared model instances."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from ..device import DeviceSelection, select_device
from ..types import Detection


class DetectorUnavailableError(RuntimeError):
    """Raised when the optional Ultralytics runtime is unavailable."""


class YOLODetector:
    """Load a YOLO model once per path/device and expose typed detections."""

    _models: ClassVar[dict[tuple[str, str], Any]] = {}
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
        self.model_path = Path(model_path)
        self.device_selection: DeviceSelection = select_device(device)
        self.confidence_threshold = confidence_threshold
        self.iou_threshold = iou_threshold
        self.target_class_ids = target_class_ids
        self._model_factory = model_factory

    @property
    def model(self) -> Any:
        key = (str(self.model_path), self.device_selection.resolved)
        with self._model_lock:
            if key not in self._models:
                factory = self._model_factory or self._default_factory
                self._models[key] = factory(str(self.model_path))
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
        if not isinstance(frame, np.ndarray) or frame.ndim not in (2, 3) or frame.size == 0:
            raise ValueError("frame must be a non-empty image array")
        results = self.model.predict(
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
                name = names[class_id] if isinstance(names, dict) else names[class_id]
                detections.append(
                    Detection(
                        bbox=tuple(float(value) for value in bbox),
                        confidence=float(confidence),
                        class_id=int(class_id),
                        class_name=str(name),
                    )
                )
        return detections
