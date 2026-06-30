"""Composable, per-camera analytics orchestration."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
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

LOGGER = logging.getLogger(__name__)

EventCallback = Callable[[AnalyticsEvent], None | Awaitable[None]]
CallbackErrorHandler = Callable[[BaseException], None]


class CallbackExecutionError(RuntimeError):
    """Raised by ``wait_for_callbacks`` when an async callback failed."""


class FramePipeline:
    """Run detector, tracking, face, zone, counting, and intrusion analytics."""

    def __init__(
        self,
        config: CVConfig,
        event_callback: EventCallback | None = None,
        *,
        detector: YOLODetector | None = None,
        face_engine: FaceEngine | None = None,
        event_loop: asyncio.AbstractEventLoop | None = None,
        callback_error_handler: CallbackErrorHandler | None = None,
        max_pending_callbacks: int = 100,
    ) -> None:
        if max_pending_callbacks < 1:
            raise ValueError("max_pending_callbacks must be positive")
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
        self.people_counter = PeopleCounter(
            config.counting_line,
            stale_after_frames=config.people_counter_stale_after_frames,
        )
        self.intrusion_detector = IntrusionDetector(
            config.intrusion_zone_ids,
            config.intrusion_cooldown_seconds,
            config.intrusion_retention_margin_seconds,
        )
        self.event_callback = event_callback
        self._event_loop = event_loop
        self._callback_error_handler = callback_error_handler
        self._max_pending_callbacks = max_pending_callbacks
        self._frame_count = 0
        self._last_faces: tuple[FaceMatch, ...] = ()
        self._executor = ThreadPoolExecutor(max_workers=config.executor_workers, thread_name_prefix="rsap-cv")
        self._process_lock = threading.Lock()
        self._last_submit = 0.0
        self._schedule_lock = threading.Lock()
        self._callback_lock = threading.Lock()
        self._callback_futures: set[Future[None]] = set()
        self._callback_errors: deque[BaseException] = deque(maxlen=100)

    def process(self, frame: np.ndarray, timestamp: datetime) -> AnalyticsResult:
        with self._process_lock:
            return self._process_locked(frame, timestamp)

    def _process_locked(self, frame: np.ndarray, timestamp: datetime) -> AnalyticsResult:
        started = time.perf_counter()
        detections: list[Detection] = self.detector.detect(frame)
        tracked = self.tracker.update_objects(detections)
        if self.face_engine is not None and self._frame_count % self.config.face_interval == 0:
            self._last_faces = tuple(self.face_engine.recognize(frame, detections))
        zone_events = self.zone_analyzer.analyze(tracked, frame.shape, timestamp)
        count_update = self.people_counter.update(tracked, frame.shape, timestamp)
        intrusions = self.intrusion_detector.check(zone_events)
        self.intrusion_detector.cleanup(timestamp, {item.track_id for item in tracked})

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
        self._bind_event_loop(loop)
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

    @property
    def callback_errors(self) -> tuple[BaseException, ...]:
        with self._callback_lock:
            return tuple(self._callback_errors)

    async def wait_for_callbacks(self) -> None:
        """Wait for scheduled callbacks and raise if any callback failed."""
        self._bind_event_loop(asyncio.get_running_loop())
        while True:
            with self._callback_lock:
                pending = tuple(self._callback_futures)
            if not pending:
                break
            await asyncio.gather(*(asyncio.wrap_future(future) for future in pending), return_exceptions=True)
        with self._callback_lock:
            errors = tuple(self._callback_errors)
            self._callback_errors.clear()
        if errors:
            raise CallbackExecutionError(f"{len(errors)} event callback(s) failed") from errors[0]

    def __enter__(self) -> FramePipeline:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _emit(self, event: AnalyticsEvent) -> None:
        if self.event_callback is None:
            return
        result = self.event_callback(event)
        if not inspect.isawaitable(result):
            return
        loop = self._event_loop
        if loop is None or not loop.is_running():
            close = getattr(result, "close", None)
            if close is not None:
                close()
            raise RuntimeError(
                "async event callbacks require process_async() or an explicit running event_loop"
            )
        with self._callback_lock:
            if len(self._callback_futures) >= self._max_pending_callbacks:
                close = getattr(result, "close", None)
                if close is not None:
                    close()
                raise RuntimeError("event callback backlog limit reached")
        future = asyncio.run_coroutine_threadsafe(self._await_callback(result), loop)
        with self._callback_lock:
            self._callback_futures.add(future)
        future.add_done_callback(self._callback_finished)

    def _bind_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._callback_lock:
            if self._event_loop is None:
                self._event_loop = loop
            elif self._event_loop is not loop:
                raise RuntimeError("FramePipeline cannot dispatch callbacks across multiple event loops")

    @staticmethod
    async def _await_callback(awaitable: Awaitable[None]) -> None:
        await awaitable

    def _callback_finished(self, future: Future[None]) -> None:
        if future.cancelled():
            with self._callback_lock:
                self._callback_futures.discard(future)
            return
        error = future.exception()
        with self._callback_lock:
            self._callback_futures.discard(future)
            if error is not None:
                self._callback_errors.append(error)
        if error is None:
            return
        LOGGER.error("event callback failed", exc_info=(type(error), error, error.__traceback__))
        if self._callback_error_handler is not None:
            try:
                self._callback_error_handler(error)
            except Exception:
                LOGGER.exception("callback error handler failed")
