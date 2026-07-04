"""One-camera capture, analytics, event, and resource lifecycle."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime

from cv_engine.pipeline import CallbackExecutionError, FramePipeline
from cv_engine.streaming import FrameBuffer, ResilientCapture

from .errors import FailureCategory, OrchestrationError, OrchestrationFailure
from .models import (
    CameraDefinition,
    CameraHealth,
    CameraMetrics,
    CameraStatus,
    RoutedAnalyticsEvent,
    RUNNING_STATES,
    StateTransition,
    VALID_TRANSITIONS,
    WorkerState,
)
from .protocols import (
    CallbackAnalyticsEvent,
    Capture,
    CaptureFactory,
    EventSink,
    Pipeline,
    PipelineFactory,
)
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
    callback: Callable[[CallbackAnalyticsEvent], Awaitable[None]],
) -> Pipeline:
    return FramePipeline(
        definition.cv_config,
        event_callback=callback,  # type: ignore[arg-type]
        max_pending_callbacks=definition.event_queue_size,
    )


class CameraWorker:
    """Own every resource and task for exactly one camera generation."""

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
        self._transition_count = 0
        self._state = WorkerState.STOPPED
        self._metrics = CameraMetrics()
        self._updated_at = datetime.now(UTC)
        self._failure: OrchestrationFailure | None = None
        self._shutdown_failures: list[OrchestrationFailure] = []
        self._stop_event = asyncio.Event()
        self._accepting_events = False
        self._active_callbacks = 0
        self._event_queue: asyncio.Queue[RoutedAnalyticsEvent | object] = asyncio.Queue(
            maxsize=definition.event_queue_size
        )
        self._capture: Capture | None = None
        self._pipeline: Pipeline | None = None
        self._capture_closed = False
        self._pipeline_closed = False
        self._supervisor_task: asyncio.Task[None] | None = None
        self._started: asyncio.Future[None] | None = None
        self._stop_lock = asyncio.Lock()
        self._analytics_started_at = time.monotonic()

    @property
    def state(self) -> WorkerState:
        return self._state

    @property
    def transitions(self) -> tuple[StateTransition, ...]:
        return tuple(self._transitions)

    @property
    def shutdown_failures(self) -> tuple[OrchestrationFailure, ...]:
        return tuple(self._shutdown_failures)

    @property
    def status(self) -> CameraStatus:
        health = (
            CameraHealth.FAILED
            if self._state is WorkerState.FAILED
            else CameraHealth.DEGRADED
            if self._failure is not None or self._metrics.event_sink_failures
            else CameraHealth.OK
        )
        return CameraStatus(
            camera_id=self.camera_id,
            generation=self.generation,
            lifecycle_state=self._state,
            health=health,
            is_running=self._state in RUNNING_STATES,
            updated_at=self._updated_at,
            last_frame_at=self._metrics.last_frame_at,
            last_event_at=self._metrics.last_event_at,
            last_processed_at=self._metrics.last_processed_at,
            failure_category=self._failure.category if self._failure else None,
            error_summary=self._failure.summary if self._failure else None,
            reconnect_count=self._metrics.reconnect_count,
            processing_fps=self._metrics.processing_fps,
            frame_buffer_size=len(self.frame_buffer),
            frame_buffer_capacity=self.definition.frame_buffer_size,
            event_queue_size=self._event_queue.qsize(),
            event_queue_capacity=self.definition.event_queue_size,
            callback_backlog=self._active_callbacks,
            dropped_event_count=self._metrics.events_dropped,
            frames_captured=self._metrics.frames_captured,
            frames_processed=self._metrics.frames_processed,
            capture_failures=self._metrics.capture_failures,
            events_emitted=self._metrics.events_emitted,
            event_sink_failures=self._metrics.event_sink_failures,
            worker_failures=self._metrics.worker_failures,
            transition_count=self._transition_count,
        )

    async def start(self) -> CameraStatus:
        if self._supervisor_task is not None:
            assert self._started is not None
            await asyncio.shield(self._started)
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
                return self.status
            if not task.done() and self._state in {
                WorkerState.STARTING,
                WorkerState.RUNNING,
                WorkerState.RECONNECTING,
            }:
                self._transition(WorkerState.STOPPING)
            self._accepting_events = False
            self._stop_event.set()
            self._close_capture_once()
            await asyncio.shield(task)
            return self.status

    async def wait(self) -> CameraStatus:
        if self._supervisor_task is not None:
            await asyncio.shield(self._supervisor_task)
        return self.status

    async def _run(self) -> None:
        capture_task: asyncio.Task[None] | None = None
        analytics_task: asyncio.Task[None] | None = None
        event_task: asyncio.Task[None] | None = None
        terminal_error: OrchestrationError | None = None
        try:
            try:
                self._capture = await asyncio.to_thread(
                    self._capture_factory, self.definition
                )
            except BaseException as error:
                raise self._public_error(FailureCategory.CAPTURE, error) from None
            if self._stop_event.is_set():
                return
            try:
                self._pipeline = await asyncio.to_thread(
                    self._pipeline_factory, self.definition, self._queue_event
                )
            except BaseException as error:
                raise self._public_error(FailureCategory.MODEL, error) from None
            if self._stop_event.is_set():
                return

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
                    raise OrchestrationError(
                        FailureCategory.INTERNAL,
                        self.camera_id,
                        "owned worker task was cancelled",
                    )
                error = task.exception()
                if error is not None:
                    category = (
                        FailureCategory.CAPTURE
                        if task is capture_task
                        else FailureCategory.PIPELINE
                    )
                    raise self._public_error(category, error) from None
            if not self._stop_event.is_set():
                raise OrchestrationError(
                    FailureCategory.INTERNAL,
                    self.camera_id,
                    "owned worker task exited unexpectedly",
                )
        except asyncio.CancelledError as error:
            terminal_error = self._public_error(FailureCategory.SHUTDOWN, error)
        except OrchestrationError as error:
            terminal_error = error
        except BaseException as error:
            terminal_error = self._public_error(FailureCategory.INTERNAL, error)
        finally:
            self._accepting_events = False
            self._stop_event.set()
            self._close_capture_once()

            worker_tasks = tuple(
                task for task in (capture_task, analytics_task) if task is not None
            )
            if worker_tasks:
                results = await asyncio.gather(*worker_tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, BaseException) and not isinstance(
                        result, asyncio.CancelledError
                    ):
                        terminal_error = terminal_error or self._public_error(
                            FailureCategory.INTERNAL, result
                        )

            pipeline_error = await self._close_pipeline_once()
            terminal_error = terminal_error or pipeline_error
            await self._settle_event_task(event_task)
            self.frame_buffer.clear()

            if terminal_error is not None:
                self._record_failure(terminal_error)
                if self._state is not WorkerState.FAILED:
                    self._transition(WorkerState.FAILED, terminal_error.public_message)
                self._reject_started(terminal_error)
            else:
                if self._state is not WorkerState.STOPPING:
                    # A normal stop always publishes STOPPING before final cleanup.
                    self._transition(WorkerState.STOPPING)
                self._transition(WorkerState.STOPPED)
                self._resolve_started()

    async def _capture_loop(self) -> None:
        assert self._capture is not None
        reconnecting = False
        while not self._stop_event.is_set():
            success, frame = await asyncio.to_thread(self._capture.read)
            if self._stop_event.is_set():
                break
            if success and frame is not None:
                self.frame_buffer.put(frame)
                self._metrics = replace(
                    self._metrics,
                    frames_captured=self._metrics.frames_captured + 1,
                    last_frame_at=datetime.now(UTC),
                )
                if reconnecting:
                    reconnecting = False
                    self._transition(WorkerState.RUNNING, "capture reconnected")
                continue

            self._metrics = replace(
                self._metrics,
                capture_failures=self._metrics.capture_failures + 1,
            )
            if not reconnecting:
                reconnecting = True
                self._metrics = replace(
                    self._metrics,
                    reconnect_count=self._metrics.reconnect_count + 1,
                )
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
                processed = self._metrics.frames_processed + 1
                elapsed = max(time.monotonic() - self._analytics_started_at, 1e-9)
                self._metrics = replace(
                    self._metrics,
                    frames_processed=processed,
                    processing_fps=processed / elapsed,
                    last_processed_at=datetime.now(UTC),
                )

    async def _queue_event(self, event: CallbackAnalyticsEvent) -> None:
        self._active_callbacks += 1
        try:
            if not self._accepting_events or self._stop_event.is_set():
                self._metrics = replace(
                    self._metrics, events_dropped=self._metrics.events_dropped + 1
                )
                return
            try:
                routed = RoutedAnalyticsEvent.from_callback(
                    self.camera_id, self.generation, event
                )
            except OrchestrationError as error:
                self._record_failure(error)
                raise
            try:
                self._event_queue.put_nowait(routed)
            except asyncio.QueueFull:
                self._metrics = replace(
                    self._metrics, events_dropped=self._metrics.events_dropped + 1
                )
                LOGGER.warning(
                    "camera event queue is full; event dropped",
                    extra={"camera_id": self.camera_id, "generation": self.generation},
                )
            else:
                self._metrics = replace(self._metrics, last_event_at=routed.timestamp)
        finally:
            self._active_callbacks -= 1

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
                    public = self._public_error(FailureCategory.SINK, error)
                    self._failure = public.failure
                    self._metrics = replace(
                        self._metrics,
                        event_sink_failures=self._metrics.event_sink_failures + 1,
                    )
                    LOGGER.error(
                        "analytics event sink failed: %s",
                        public.public_message,
                        extra={
                            "camera_id": self.camera_id,
                            "generation": self.generation,
                        },
                    )
                else:
                    self._metrics = replace(
                        self._metrics, events_emitted=self._metrics.events_emitted + 1
                    )
            finally:
                self._event_queue.task_done()

    def _close_capture_once(self) -> None:
        if self._capture_closed or self._capture is None:
            return
        self._capture_closed = True
        try:
            self._capture.close()
        except Exception as error:
            public = self._public_error(FailureCategory.SHUTDOWN, error)
            self._shutdown_failures.append(public.failure)
            self._failure = public.failure
            LOGGER.error(
                "camera capture close failed: %s",
                public.public_message,
                extra={"camera_id": self.camera_id, "generation": self.generation},
            )

    async def _close_pipeline_once(self) -> OrchestrationError | None:
        if self._pipeline_closed or self._pipeline is None:
            return None
        self._pipeline_closed = True
        try:
            await self._pipeline.aclose(cancel_pending=False)
        except CallbackExecutionError as error:
            public = self._public_error(FailureCategory.CALLBACK, error)
        except Exception as error:
            public = self._public_error(FailureCategory.SHUTDOWN, error)
        else:
            return None
        self._shutdown_failures.append(public.failure)
        return public

    async def _settle_event_task(self, event_task: asyncio.Task[None] | None) -> None:
        if event_task is None:
            return
        if event_task.done():
            try:
                event_task.result()
            except Exception as error:
                public = self._public_error(FailureCategory.INTERNAL, error)
                self._shutdown_failures.append(public.failure)
            while True:
                try:
                    self._event_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                else:
                    self._event_queue.task_done()
            return
        await self._event_queue.join()
        await self._event_queue.put(_EVENT_STOP)
        await event_task

    def _record_failure(self, error: OrchestrationError) -> None:
        self._failure = error.failure
        self._metrics = replace(
            self._metrics, worker_failures=self._metrics.worker_failures + 1
        )
        LOGGER.error(
            "camera worker failed [%s]: %s",
            error.category.value,
            error.public_message,
            extra={"camera_id": self.camera_id, "generation": self.generation},
        )

    def _public_error(
        self, category: FailureCategory, error: BaseException | str
    ) -> OrchestrationError:
        if isinstance(error, OrchestrationError):
            return error
        return OrchestrationError(
            category,
            self.camera_id,
            sanitize_error(error),
            cause=error if isinstance(error, BaseException) else None,
        )

    def _transition(self, state: WorkerState, reason: str | None = None) -> None:
        if state is self._state:
            return
        if state not in VALID_TRANSITIONS[self._state]:
            raise OrchestrationError(
                FailureCategory.INTERNAL,
                self.camera_id,
                f"invalid lifecycle transition {self._state.value} -> {state.value}",
            )
        previous = self._state
        self._state = state
        self._updated_at = datetime.now(UTC)
        self._transition_count += 1
        self._transitions.append(
            StateTransition(
                previous,
                state,
                self._updated_at,
                sanitize_error(reason) if reason else None,
            )
        )
        if self._transition_listener is not None:
            try:
                self._transition_listener(self.status)
            except Exception as error:
                LOGGER.error(
                    "camera transition listener failed: %s",
                    sanitize_error(error),
                    extra={"camera_id": self.camera_id, "generation": self.generation},
                )

    def _resolve_started(self) -> None:
        if self._started is not None and not self._started.done():
            self._started.set_result(None)

    def _reject_started(self, error: OrchestrationError) -> None:
        if self._started is not None and not self._started.done():
            self._started.set_exception(error)
