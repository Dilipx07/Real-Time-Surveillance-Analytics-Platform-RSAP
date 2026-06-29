"""Composable, per-camera analytics orchestration."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np

from ..config import CVConfig
from ..models.detector import YOLODetector
from ..models.face_engine import FaceEngine
from ..models.tracker import Sort
from ..types import (
    AnalyticsEvent,
    AnalyticsResult,
    CountDirection,
    Detection,
    FaceMatch,
    OverlayData,
)
from .intrusion_detector import IntrusionDetector
from .people_counter import PeopleCounter
from .zone_analyzer import ZoneAnalyzer

EventCallback = Callable[[AnalyticsEvent], None]


class FramePipeline:
    """Run detector, tracking, face, zone, counting, and intrusion analytics."""

    def __init__(
        self,
        config: CVConfig,
        event_callback: EventCallback | None = None,
        *,
        detector: YOLODetector | None = None,
        face_engine: FaceEngine | None = None,
    ) -> None:
        self.config = config
        self.detector = detector or YOLODetector(
            config.model_path,
            device=config.device,
            confidence_threshold=config.confidence_threshold,
            iou_threshold=config.iou_threshold,
            target_class_ids=config.target_class_ids,
        )
        self.face_engine = face_engine or (FaceEngine(config.face_tolerance) if config.face_recognition else None)
        self.tracker = Sort(config.tracker_max_age, config.tracker_min_hits, config.tracker_iou_threshold)
        self.zone_analyzer = ZoneAnalyzer(config.zones)
        self.people_counter = PeopleCounter(config.counting_line)
        self.intrusion_detector = IntrusionDetector(config.intrusion_zone_ids, config.intrusion_cooldown_seconds)
        self.event_callback = event_callback
        self._frame_count = 0
        self._last_faces: tuple[FaceMatch, ...] = ()
        self._executor = ThreadPoolExecutor(max_workers=config.executor_workers, thread_name_prefix="rsap-cv")
        self._last_submit = 0.0
        self._schedule_lock = threading.Lock()

    def process(self, frame: np.ndarray, timestamp: datetime) -> AnalyticsResult:
        started = time.perf_counter()
        detections: list[Detection] = self.detector.detect(frame)
        tracked = self.tracker.update_objects(detections)
        if self.face_engine is not None and self._frame_count % self.config.face_interval == 0:
            self._last_faces = tuple(self.face_engine.recognize(frame, detections))
        zone_events = self.zone_analyzer.analyze(tracked, frame.shape, timestamp)
        count_update = self.people_counter.update(tracked, frame.shape, timestamp)
        intrusions = self.intrusion_detector.check(zone_events)

        for event in zone_events:
            self._emit(AnalyticsEvent(f"zone_{event.event_type.value}", timestamp, event.track_id, event.zone_id, {"zone_name": event.zone_name}))
        for event in count_update.events:
            self._emit(AnalyticsEvent(f"count_{event.direction.value}", timestamp, event.track_id, None, {"count_in": count_update.count_in, "count_out": count_update.count_out}))
        for event in intrusions:
            self._emit(AnalyticsEvent("intrusion", timestamp, event.track_id, event.zone_id, {"zone_name": event.zone_name, "confidence": event.confidence}))

        self._frame_count += 1
        overlay = OverlayData(tuple(tracked), self.config.zones, self._last_faces, count_update.count_in, count_update.count_out)
        return AnalyticsResult(
            timestamp,
            tuple(detections),
            tuple(tracked),
            self._last_faces,
            tuple(zone_events),
            count_update,
            tuple(intrusions),
            overlay,
            (time.perf_counter() - started) * 1000.0,
        )

    async def process_async(self, frame: np.ndarray, timestamp: datetime) -> AnalyticsResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self.process, frame, timestamp)

    async def process_if_due(self, frame: np.ndarray, timestamp: datetime) -> AnalyticsResult | None:
        now = time.monotonic()
        with self._schedule_lock:
            if now - self._last_submit < 1.0 / self.config.analytics_fps:
                return None
            self._last_submit = now
        return await self.process_async(frame, timestamp)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def __enter__(self) -> FramePipeline:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _emit(self, event: AnalyticsEvent) -> None:
        if self.event_callback is not None:
            self.event_callback(event)
