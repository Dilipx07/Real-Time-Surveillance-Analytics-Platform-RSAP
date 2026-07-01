"""Composable, per-camera analytics orchestration."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from concurrent.futures import Future as ConcurrentFuture
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
        self._lifecycle_condition = threading.Condition()
        self._active_processes = 0
        self._closing = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._last_submit = 0.0
        self._schedule_lock = threading.Lock()
        self._callback_lock = threading.Lock()
        self._callback_condition = threading.Condition(self._callback_lock)
        self._callback_futures: set[asyncio.Task[None]] = set()
        self._callback_errors: deque[BaseException] = deque(maxlen=100)
        self._accepting_callbacks = True
        self._active_callback_submissions = 0
        self._callback_reservations = 0

    def process(self, frame: np.ndarray, timestamp: datetime) -> AnalyticsResult:
        with self._lifecycle_condition:
            if self._closing or self._closed:
                raise RuntimeError("FramePipeline is closing or closed")
            self._active_processes += 1
        try:
            with self._process_lock:
                return self._process_locked(frame, timestamp)
        finally:
            with self._lifecycle_condition:
                self._active_processes -= 1
                self._lifecycle_condition.notify_all()

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
        self._ensure_open()
        loop = asyncio.get_running_loop()
        self._bind_event_loop(loop)
        return await loop.run_in_executor(self._executor, self.process, frame, timestamp)

    async def process_if_due(self, frame: np.ndarray, timestamp: datetime) -> AnalyticsResult | None:
        self._ensure_open()
        now = time.monotonic()
        with self._schedule_lock:
            if now - self._last_submit < 1.0 / self.config.analytics_fps:
                return None
            self._last_submit = now
        return await self.process_async(frame, timestamp)

    def close(self) -> None:
        """Close synchronous pipelines; async callbacks require :meth:`aclose`."""
        with self._lifecycle_condition:
            if self._closed:
                return
            if self._close_task is not None:
                raise RuntimeError("asynchronous shutdown is in progress; await aclose()")
            self._closing = True
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._wait_for_active_processes()
        with self._callback_lock:
            self._accepting_callbacks = False
            pending_count = len(self._callback_futures)
            errors = tuple(self._callback_errors)
        if pending_count:
            raise RuntimeError(
                f"{pending_count} async callback(s) are pending; await aclose() instead"
            )
        with self._lifecycle_condition:
            self._closed = True
            self._lifecycle_condition.notify_all()
        if errors:
            raise CallbackExecutionError(f"{len(errors)} event callback(s) failed") from errors[0]

    async def aclose(self, *, cancel_pending: bool = False) -> None:
        """Stop processing and deterministically drain or cancel async callbacks."""
        loop = asyncio.get_running_loop()
        current_task = asyncio.current_task()
        with self._callback_condition:
            if current_task in self._callback_futures:
                raise RuntimeError(
                    "aclose() cannot be awaited from its own event callback; "
                    "schedule shutdown in a separate task and return from the callback"
                )
        with self._lifecycle_condition:
            if self._closed:
                return
            self._bind_event_loop(loop)
            close_task = self._close_task
            if close_task is None:
                self._closing = True
                with self._callback_condition:
                    self._accepting_callbacks = False
                close_task = loop.create_task(self._aclose_impl(cancel_pending))
                self._close_task = close_task
        await asyncio.shield(close_task)

    async def _aclose_impl(self, cancel_pending: bool) -> None:
        errors: tuple[BaseException, ...] = ()
        try:
            await asyncio.to_thread(self._wait_for_active_processes)
            await asyncio.to_thread(self._wait_for_callback_submissions)
            with self._callback_condition:
                pending = tuple(self._callback_futures)
            if cancel_pending:
                for task in pending:
                    task.cancel()
            await self._settle_callbacks()
            with self._callback_condition:
                if self._active_callback_submissions or self._callback_futures:
                    raise RuntimeError("callback shutdown did not settle all submissions")
                errors = tuple(self._callback_errors)
                self._callback_errors.clear()
            await asyncio.to_thread(
                self._executor.shutdown,
                wait=True,
                cancel_futures=False,
            )
        finally:
            with self._lifecycle_condition:
                self._closed = True
                self._lifecycle_condition.notify_all()
        if errors:
            raise CallbackExecutionError(f"{len(errors)} event callback(s) failed") from errors[0]

    @property
    def callback_errors(self) -> tuple[BaseException, ...]:
        with self._callback_lock:
            return tuple(self._callback_errors)

    async def wait_for_callbacks(self) -> None:
        """Wait for scheduled callbacks and raise if any callback failed."""
        self._bind_event_loop(asyncio.get_running_loop())
        current_task = asyncio.current_task()
        with self._callback_condition:
            if current_task in self._callback_futures:
                raise RuntimeError("wait_for_callbacks() cannot be awaited from its own event callback")
        await asyncio.to_thread(self._wait_for_callback_submissions)
        await self._settle_callbacks()
        with self._callback_lock:
            errors = tuple(self._callback_errors)
            self._callback_errors.clear()
        if errors:
            raise CallbackExecutionError(f"{len(errors)} event callback(s) failed") from errors[0]

    async def _settle_callbacks(self) -> None:
        while True:
            with self._callback_lock:
                pending = tuple(self._callback_futures)
            if not pending:
                break
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.sleep(0)

    def __enter__(self) -> FramePipeline:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _emit(self, event: AnalyticsEvent) -> None:
        if self.event_callback is None:
            return
        with self._callback_condition:
            if not self._accepting_callbacks:
                raise RuntimeError("FramePipeline is no longer accepting callbacks")
            self._active_callback_submissions += 1
        try:
            result = self.event_callback(event)
            if not inspect.isawaitable(result):
                return
            with self._callback_condition:
                loop = self._event_loop
                if loop is None or not loop.is_running():
                    close = getattr(result, "close", None)
                    if close is not None:
                        close()
                    raise RuntimeError(
                        "async event callbacks require process_async() or an explicit running event_loop"
                    )
                if len(self._callback_futures) + self._callback_reservations >= self._max_pending_callbacks:
                    close = getattr(result, "close", None)
                    if close is not None:
                        close()
                    raise RuntimeError("event callback backlog limit reached")
                self._callback_reservations += 1
            try:
                task = self._create_callback_task(result, loop)
            except BaseException:
                close = getattr(result, "close", None)
                if close is not None:
                    close()
                raise
            finally:
                with self._callback_condition:
                    self._callback_reservations -= 1
            with self._callback_condition:
                self._callback_futures.add(task)
            task.add_done_callback(self._callback_finished)
        finally:
            with self._callback_condition:
                self._active_callback_submissions -= 1
                self._callback_condition.notify_all()

    def _ensure_open(self) -> None:
        with self._lifecycle_condition:
            if self._closing or self._closed:
                raise RuntimeError("FramePipeline is closing or closed")

    def _wait_for_active_processes(self) -> None:
        with self._lifecycle_condition:
            self._lifecycle_condition.wait_for(lambda: self._active_processes == 0)

    def _wait_for_callback_submissions(self) -> None:
        with self._callback_condition:
            self._callback_condition.wait_for(lambda: self._active_callback_submissions == 0)

    def _bind_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._callback_lock:
            if self._event_loop is None:
                self._event_loop = loop
            elif self._event_loop is not loop:
                raise RuntimeError("FramePipeline cannot dispatch callbacks across multiple event loops")

    async def _await_callback(self, awaitable: Awaitable[None]) -> None:
        task = asyncio.current_task()
        if task is not None:
            with self._callback_condition:
                self._callback_futures.add(task)
        await awaitable

    def _create_callback_task(
        self,
        awaitable: Awaitable[None],
        loop: asyncio.AbstractEventLoop,
    ) -> asyncio.Task[None]:
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            return loop.create_task(self._await_callback(awaitable))

        created: ConcurrentFuture[asyncio.Task[None]] = ConcurrentFuture()

        def create() -> None:
            try:
                created.set_result(loop.create_task(self._await_callback(awaitable)))
            except BaseException as error:
                created.set_exception(error)

        loop.call_soon_threadsafe(create)
        return created.result()

    def _callback_finished(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            with self._callback_lock:
                self._callback_futures.discard(task)
            return
        error = task.exception()
        with self._callback_lock:
            self._callback_futures.discard(task)
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
