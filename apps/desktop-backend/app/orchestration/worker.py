"""One-camera capture, analytics, and event-routing lifecycle."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from cv_engine import AnalyticsEvent
from cv_engine.pipeline import CallbackExecutionError, FramePipeline
from cv_engine.streaming import FrameBuffer, ResilientCapture

from .models import (
    CameraDefinition,
    CameraMetrics,
    CameraStatus,
    RoutedAnalyticsEvent,
    StateTransition,
    WorkerState,
)
from .protocols import Capture, CaptureFactory, EventSink, Pipeline, PipelineFactory
from .security import sanitize_error

LOGGER = logging.getLogger(__name__)
_EVENT_STOP = object()


def default_capture_factory(definition: CameraDefinition) -> Capture:
    return ResilientCapture(
        definition.source,
        reconnect_delay=definition.reconnect_delay_seconds,
    )


def default_pipeline_factory(
    definition: CameraDefinition,
    callback: Callable[[AnalyticsEvent], Awaitable[None]],
) -> Pipeline:
    return FramePipeline(
        definition.cv_config,
        event_callback=callback,
        max_pending_callbacks=definition.event_queue_size,
    )


class CameraWorker:
    """Own all resources and tasks for one camera generation."""

    def __init__(
        self,
        definition: CameraDefinition,
        generation: int,
        event_sink: EventSink,
        *,
        capture_factory: CaptureFactory = default_capture_factory,
        pipeline_factory: PipelineFactory = default_pipeline_factory,
        transition_listener: Callable[[CameraStatus], None] | None = None,
        transition_history_size: int = 64,
    ) -> None:
        if generation < 1:
            raise ValueError("generation must be positive")
        if transition_history_size < 1:
            raise ValueError("transition_history_size must be positive")
        self.definition = definition
        self.camera_id = definition.camera_id
        self.generation = generation
        self.frame_buffer = FrameBuffer(definition.frame_buffer_size)
        self._event_sink = event_sink
        self._capture_factory = capture_factory
        self._pipeline_factory = pipeline_factory
        self._transition_listener = transition_listener
        self._transitions: deque[StateTransition] = deque(
            maxlen=transition_history_size
        )
        self._state = WorkerState.STOPPED
        self._metrics = CameraMetrics()
        self._updated_at = datetime.now(UTC)
        self._last_error: str | None = None
        self._stop_event = asyncio.Event()
        self._accepting_events = False
        self._event_queue: asyncio.Queue[RoutedAnalyticsEvent | object] = asyncio.Queue(
            maxsize=definition.event_queue_size
        )
        self._capture: Capture | None = None
        self._pipeline: Pipeline | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        self._started: asyncio.Future[None] | None = None
        self._stop_lock = asyncio.Lock()

    @property
    def state(self) -> WorkerState:
        return self._state

    @property
    def transitions(self) -> tuple[StateTransition, ...]:
        return tuple(self._transitions)

    @property
    def status(self) -> CameraStatus:
        return CameraStatus(
            camera_id=self.camera_id,
            state=self._state,
            generation=self.generation,
            metrics=self._metrics,
            updated_at=self._updated_at,
            last_error=self._last_error,
            transition_count=len(self._transitions),
        )

    async def start(self) -> CameraStatus:
        if self._supervisor_task is not None:
            assert self._started is not None
            await self._started
            return self.status
        loop = asyncio.get_running_loop()
        self._started = loop.create_future()
        self._transition(WorkerState.STARTING)
        self._supervisor_task = loop.create_task(
            self._run(), name=f"camera-supervisor:{self.camera_id}:{self.generation}"
        )
        try:
            await asyncio.shield(self._started)
        except asyncio.CancelledError:
            await self.stop()
            raise
        return self.status

    async def stop(self) -> CameraStatus:
        async with self._stop_lock:
            task = self._supervisor_task
            if task is None:
                if self._state is not WorkerState.STOPPED:
                    self._transition(WorkerState.STOPPED)
                return self.status
            if not task.done() and self._state not in {
                WorkerState.STOPPING,
                WorkerState.FAILED,
            }:
                self._transition(WorkerState.STOPPING)
            self._accepting_events = False
            self._stop_event.set()
            if self._capture is not None:
                self._capture.close()
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                raise
            except Exception:
                # _run records and logs the failure. Stop remains idempotent and
                # callers inspect FAILED through the status contract.
                pass
            return self.status

    async def wait(self) -> CameraStatus:
        if self._supervisor_task is not None:
            try:
                await asyncio.shield(self._supervisor_task)
            except Exception:
                pass
        return self.status

    async def _run(self) -> None:
        capture_task: asyncio.Task[None] | None = None
        analytics_task: asyncio.Task[None] | None = None
        event_task: asyncio.Task[None] | None = None
        failure: BaseException | None = None
        try:
            self._capture = self._capture_factory(self.definition)
            self._pipeline = self._pipeline_factory(self.definition, self._queue_event)
            self._accepting_events = True
            event_task = asyncio.create_task(
                self._dispatch_events(),
                name=f"camera-events:{self.camera_id}:{self.generation}",
            )
            capture_task = asyncio.create_task(
                self._capture_loop(),
                name=f"camera-capture:{self.camera_id}:{self.generation}",
            )
            analytics_task = asyncio.create_task(
                self._analytics_loop(),
                name=f"camera-analytics:{self.camera_id}:{self.generation}",
            )
            self._transition(WorkerState.RUNNING)
            self._resolve_started()

            done, _ = await asyncio.wait(
                {capture_task, analytics_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task.cancelled():
                    raise asyncio.CancelledError
                error = task.exception()
                if error is not None:
                    raise error
            if not self._stop_event.is_set():
                raise RuntimeError("owned worker task exited unexpectedly")
        except asyncio.CancelledError as error:
            failure = error
            raise
        except BaseException as error:
            failure = error
            self._record_worker_failure(error)
            self._reject_started(error)
        finally:
            self._accepting_events = False
            self._stop_event.set()
            if self._capture is not None:
                try:
                    self._capture.close()
                except Exception as error:
                    failure = failure or error
                    self._record_worker_failure(error)

            worker_tasks = tuple(
                task for task in (capture_task, analytics_task) if task is not None
            )
            if worker_tasks:
                results = await asyncio.gather(*worker_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException) and not isinstance(
                        result, asyncio.CancelledError
                    ):
                        if failure is None:
                            failure = result
                            self._record_worker_failure(result)

            if self._pipeline is not None:
                try:
                    await self._pipeline.aclose(cancel_pending=False)
                except CallbackExecutionError as error:
                    failure = failure or error
                    self._record_worker_failure(error)
                except Exception as error:
                    failure = failure or error
                    self._record_worker_failure(error)

            if event_task is not None:
                await self._event_queue.join()
                await self._event_queue.put(_EVENT_STOP)
                await event_task

            self.frame_buffer.clear()
            self._resolve_started()
            if failure is None or isinstance(failure, asyncio.CancelledError):
                self._transition(WorkerState.STOPPED)
            else:
                self._transition(WorkerState.FAILED, sanitize_error(failure))

    async def _capture_loop(self) -> None:
        assert self._capture is not None
        reconnecting = False
        while not self._stop_event.is_set():
            success, frame = await asyncio.to_thread(self._capture.read)
            if self._stop_event.is_set():
                break
            if success and frame is not None:
                self.frame_buffer.put(frame)
                now = datetime.now(UTC)
                self._metrics = self._metrics.increment(
                    frames_captured=1, last_frame_at=now
                )
                if reconnecting:
                    reconnecting = False
                    self._transition(WorkerState.RUNNING, "capture reconnected")
                continue

            reconnecting = True
            self._metrics = self._metrics.increment(capture_failures=1)
            if self._state is not WorkerState.RECONNECTING:
                self._transition(WorkerState.RECONNECTING, "capture unavailable")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.definition.reconnect_delay_seconds,
                )
            except TimeoutError:
                pass

    async def _analytics_loop(self) -> None:
        assert self._pipeline is not None
        sequence: int | None = None
        while not self._stop_event.is_set():
            latest = await asyncio.to_thread(
                self.frame_buffer.wait_for_frame_with_sequence,
                self.definition.frame_wait_seconds,
                sequence,
            )
            if latest is None or self._stop_event.is_set():
                continue
            sequence, frame = latest
            result = await self._pipeline.process_if_due(frame, datetime.now(UTC))
            if result is not None:
                self._metrics = self._metrics.increment(
                    frames_processed=1, last_processed_at=datetime.now(UTC)
                )

    async def _queue_event(self, event: AnalyticsEvent) -> None:
        if not self._accepting_events or self._stop_event.is_set():
            self._metrics = self._metrics.increment(events_dropped=1)
            return
        routed = RoutedAnalyticsEvent(self.camera_id, self.generation, event)
        try:
            self._event_queue.put_nowait(routed)
        except asyncio.QueueFull:
            self._metrics = self._metrics.increment(events_dropped=1)
            LOGGER.warning(
                "camera event queue is full; event dropped",
                extra={"camera_id": self.camera_id, "generation": self.generation},
            )

    async def _dispatch_events(self) -> None:
        while True:
            item = await self._event_queue.get()
            try:
                if item is _EVENT_STOP:
                    return
                assert isinstance(item, RoutedAnalyticsEvent)
                try:
                    async with asyncio.timeout(
                        self.definition.event_sink_timeout_seconds
                    ):
                        await self._event_sink.emit(item)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._metrics = self._metrics.increment(event_sink_failures=1)
                    self._last_error = sanitize_error(error)
                    LOGGER.error(
                        "analytics event sink failed: %s",
                        sanitize_error(error),
                        extra={
                            "camera_id": self.camera_id,
                            "generation": self.generation,
                        },
                    )
                else:
                    self._metrics = self._metrics.increment(events_emitted=1)
            finally:
                self._event_queue.task_done()

    def _record_worker_failure(self, error: BaseException) -> None:
        rendered = sanitize_error(error)
        self._last_error = rendered
        self._metrics = self._metrics.increment(worker_failures=1)
        LOGGER.error(
            "camera worker failed: %s",
            rendered,
            extra={"camera_id": self.camera_id, "generation": self.generation},
        )

    def _transition(self, state: WorkerState, reason: str | None = None) -> None:
        if state is self._state and reason is None:
            return
        previous = self._state
        self._state = state
        self._updated_at = datetime.now(UTC)
        safe_reason = sanitize_error(reason) if reason else None
        self._transitions.append(
            StateTransition(previous, state, self._updated_at, safe_reason)
        )
        if self._transition_listener is not None:
            try:
                self._transition_listener(self.status)
            except Exception:
                LOGGER.error(
                    "camera transition listener failed",
                    extra={"camera_id": self.camera_id, "generation": self.generation},
                )

    def _resolve_started(self) -> None:
        if self._started is not None and not self._started.done():
            self._started.set_result(None)

    def _reject_started(self, error: BaseException) -> None:
        if self._started is not None and not self._started.done():
            self._started.set_exception(error)
