"""Per-camera serialized lifecycle manager with cancellation-safe shutdown."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from weakref import WeakValueDictionary

from cv_engine.streaming import FrameBuffer

from .errors import (
    FailureCategory,
    OrchestrationError,
    OrchestrationFailure,
    ShutdownError,
)
from .models import (
    CameraDefinition,
    CameraStatus,
    LifecycleOperation,
    LifecycleOperationResult,
    LifecycleOutcome,
    OrchestrationHealth,
    RUNNING_STATES,
    WorkerState,
)
from .protocols import CaptureFactory, EventSink, PipelineFactory
from .worker import CameraWorker, default_capture_factory, default_pipeline_factory


class CameraWorkerManager:
    """Own workers atomically while allowing unrelated cameras to progress."""

    def __init__(
        self,
        event_sink: EventSink,
        *,
        capture_factory: CaptureFactory = default_capture_factory,
        pipeline_factory: PipelineFactory = default_pipeline_factory,
        retained_statuses: int = 256,
        max_active_workers: int = 8,
        transition_listener: Callable[[CameraStatus], None] | None = None,
    ) -> None:
        if retained_statuses < 1:
            raise ValueError("retained_statuses must be positive")
        if max_active_workers < 1:
            raise ValueError("max_active_workers must be positive")
        self._event_sink = event_sink
        self._capture_factory = capture_factory
        self._pipeline_factory = pipeline_factory
        self._retained_statuses = retained_statuses
        self._max_active_workers = max_active_workers
        self._external_transition_listener = transition_listener
        self._workers: dict[str, CameraWorker] = {}
        self._statuses: OrderedDict[str, CameraStatus] = OrderedDict()
        self._generations: OrderedDict[str, int] = OrderedDict()
        self._camera_locks: WeakValueDictionary[str, asyncio.Lock] = (
            WeakValueDictionary()
        )
        self._registry_lock = asyncio.Lock()
        self._admission_closed = False
        self._shutdown_task: asyncio.Task[None] | None = None
        self._shutdown_error: ShutdownError | None = None

    async def start_camera(
        self, definition: CameraDefinition
    ) -> LifecycleOperationResult:
        camera_lock = await self._get_camera_lock(definition.camera_id)
        async with camera_lock:
            current = await self._get_owned_worker(definition.camera_id)
            if current is not None and current.state in RUNNING_STATES | {
                WorkerState.STARTING
            }:
                return self._result(
                    LifecycleOperation.START,
                    LifecycleOutcome.ALREADY_RUNNING,
                    current.status,
                )

            if current is not None:
                await current.stop()
                self._remember(current.status)
                await self._remove_worker(definition.camera_id, current)
                if current.shutdown_failures:
                    raise ShutdownError(current.shutdown_failures)

            async with self._registry_lock:
                self._ensure_admission_open(definition.camera_id)
                if len(self._workers) >= self._max_active_workers:
                    raise OrchestrationError(
                        FailureCategory.CAPACITY,
                        definition.camera_id,
                        f"active camera capacity {self._max_active_workers} reached",
                    )
                worker = self._new_worker(definition)
                self._workers[definition.camera_id] = worker

            try:
                status = await worker.start()
            except BaseException:
                await worker.wait()
                self._remember(worker.status)
                await self._remove_worker(definition.camera_id, worker)
                raise
            if await self._is_admission_closed():
                await worker.stop()
                self._remember(worker.status)
                await self._remove_worker(definition.camera_id, worker)
                self._ensure_admission_open(definition.camera_id)
            self._remember(status)
            return self._result(
                LifecycleOperation.START, LifecycleOutcome.STARTED, status
            )

    async def stop_camera(self, camera_id: str) -> LifecycleOperationResult:
        camera_lock = await self._get_camera_lock(camera_id)
        async with camera_lock:
            worker = await self._get_owned_worker(camera_id)
            if worker is None:
                return self._result(
                    LifecycleOperation.STOP,
                    LifecycleOutcome.ALREADY_STOPPED,
                    self._statuses.get(camera_id),
                    camera_id=camera_id,
                )
            status = await worker.stop()
            self._remember(status)
            await self._remove_worker(camera_id, worker)
            outcome = (
                LifecycleOutcome.FAILED
                if status.state is WorkerState.FAILED or worker.shutdown_failures
                else LifecycleOutcome.STOPPED
            )
            return self._result(LifecycleOperation.STOP, outcome, status)

    async def restart_camera(
        self, definition: CameraDefinition
    ) -> LifecycleOperationResult:
        camera_lock = await self._get_camera_lock(definition.camera_id)
        async with camera_lock:
            current = await self._get_owned_worker(definition.camera_id)
            if current is not None:
                await current.stop()
                self._remember(current.status)
                await self._remove_worker(definition.camera_id, current)
                if current.shutdown_failures:
                    raise ShutdownError(current.shutdown_failures)

            async with self._registry_lock:
                self._ensure_admission_open(definition.camera_id)
                if len(self._workers) >= self._max_active_workers:
                    raise OrchestrationError(
                        FailureCategory.CAPACITY,
                        definition.camera_id,
                        f"active camera capacity {self._max_active_workers} reached",
                    )
                worker = self._new_worker(definition)
                self._workers[definition.camera_id] = worker
            try:
                status = await worker.start()
            except BaseException:
                await worker.wait()
                self._remember(worker.status)
                await self._remove_worker(definition.camera_id, worker)
                raise
            if await self._is_admission_closed():
                await worker.stop()
                self._remember(worker.status)
                await self._remove_worker(definition.camera_id, worker)
                self._ensure_admission_open(definition.camera_id)
            self._remember(status)
            return self._result(
                LifecycleOperation.RESTART, LifecycleOutcome.RESTARTED, status
            )

    async def reconcile(
        self, desired: list[CameraDefinition]
    ) -> dict[str, CameraStatus]:
        desired_by_id = {item.camera_id: item for item in desired}
        if len(desired_by_id) != len(desired):
            raise OrchestrationError(
                FailureCategory.CONFIGURATION,
                None,
                "camera catalog returned duplicate camera IDs",
            )
        async with self._registry_lock:
            self._ensure_admission_open(None)
            current = dict(self._workers)

        removals = [
            self.stop_camera(camera_id)
            for camera_id in current
            if camera_id not in desired_by_id
        ]
        if removals:
            raw_removal_results = await asyncio.gather(
                *removals, return_exceptions=True
            )
            removal_errors = tuple(
                result
                for result in raw_removal_results
                if isinstance(result, BaseException)
            )
            if removal_errors:
                error = removal_errors[0]
                if isinstance(error, OrchestrationError):
                    raise error
                raise OrchestrationError(
                    FailureCategory.SHUTDOWN, None, error, cause=error
                ) from None
            removal_results = tuple(
                result
                for result in raw_removal_results
                if isinstance(result, LifecycleOperationResult)
            )
            failures = tuple(
                result.error
                for result in removal_results
                if result.outcome is LifecycleOutcome.FAILED
                and result.error is not None
            )
            if failures:
                raise ShutdownError(failures)

        operations = []
        for camera_id, definition in desired_by_id.items():
            worker = await self._get_owned_worker(camera_id)
            if worker is None:
                operations.append(self.start_camera(definition))
            elif worker.definition != definition or worker.state not in RUNNING_STATES:
                operations.append(self.restart_camera(definition))
        if operations:
            operation_results = await asyncio.gather(
                *operations, return_exceptions=True
            )
            errors = tuple(
                result
                for result in operation_results
                if isinstance(result, BaseException)
            )
            if errors:
                error = errors[0]
                if isinstance(error, OrchestrationError):
                    raise error
                raise OrchestrationError(
                    FailureCategory.INTERNAL, None, error, cause=error
                ) from None
        return self.statuses()

    def get_frame_buffer(self, camera_id: str) -> FrameBuffer:
        worker = self._workers.get(camera_id)
        if worker is None or worker.state not in RUNNING_STATES:
            raise KeyError(f"camera is not running: {camera_id}")
        return worker.frame_buffer

    def get_status(self, camera_id: str) -> CameraStatus | None:
        worker = self._workers.get(camera_id)
        return worker.status if worker is not None else self._statuses.get(camera_id)

    def statuses(self) -> dict[str, CameraStatus]:
        statuses = dict(self._statuses)
        statuses.update(
            {camera_id: worker.status for camera_id, worker in self._workers.items()}
        )
        return statuses

    def active_camera_ids(self) -> tuple[str, ...]:
        return tuple(
            camera_id
            for camera_id, worker in self._workers.items()
            if worker.state
            in RUNNING_STATES | {WorkerState.STARTING, WorkerState.STOPPING}
        )

    def health(self) -> OrchestrationHealth:
        return OrchestrationHealth.from_statuses(self.statuses())

    async def shutdown(self) -> None:
        async with self._registry_lock:
            self._admission_closed = True
            if self._shutdown_task is None:
                self._shutdown_task = asyncio.create_task(
                    self._shutdown_impl(), name="camera-manager-shutdown"
                )
            shutdown_task = self._shutdown_task
        await asyncio.shield(shutdown_task)

    async def _shutdown_impl(self) -> None:
        failures: list[OrchestrationFailure] = []
        async with self._registry_lock:
            camera_ids = tuple(self._workers)
        results = await asyncio.gather(
            *(self._shutdown_camera(camera_id) for camera_id in camera_ids),
            return_exceptions=True,
        )
        for camera_id, result in zip(camera_ids, results, strict=True):
            if isinstance(result, BaseException):
                failures.append(
                    OrchestrationFailure(
                        FailureCategory.SHUTDOWN,
                        OrchestrationError(
                            FailureCategory.SHUTDOWN, camera_id, result
                        ).public_message,
                    )
                )
            else:
                failures.extend(result)
        if failures:
            self._shutdown_error = ShutdownError(tuple(failures))
            raise self._shutdown_error

    async def _shutdown_camera(
        self, camera_id: str
    ) -> tuple[OrchestrationFailure, ...]:
        camera_lock = await self._get_camera_lock(camera_id)
        async with camera_lock:
            worker = await self._get_owned_worker(camera_id)
            if worker is None:
                return ()
            try:
                await worker.stop()
            except BaseException as error:
                await worker.wait()
                public = OrchestrationError(
                    FailureCategory.SHUTDOWN, camera_id, error, cause=error
                )
                failures = (public.failure, *worker.shutdown_failures)
            else:
                failures = worker.shutdown_failures
            self._remember(worker.status)
            await self._remove_worker(camera_id, worker)
            return tuple(failures)

    async def _get_camera_lock(self, camera_id: str) -> asyncio.Lock:
        async with self._registry_lock:
            lock = self._camera_locks.get(camera_id)
            if lock is None:
                lock = asyncio.Lock()
                self._camera_locks[camera_id] = lock
            return lock

    async def _get_owned_worker(self, camera_id: str) -> CameraWorker | None:
        async with self._registry_lock:
            return self._workers.get(camera_id)

    async def _is_admission_closed(self) -> bool:
        async with self._registry_lock:
            return self._admission_closed

    async def _remove_worker(self, camera_id: str, worker: CameraWorker) -> None:
        async with self._registry_lock:
            if self._workers.get(camera_id) is worker:
                self._workers.pop(camera_id)

    def _new_worker(self, definition: CameraDefinition) -> CameraWorker:
        generation = self._generations.get(definition.camera_id, 0) + 1
        self._generations[definition.camera_id] = generation
        self._generations.move_to_end(definition.camera_id)
        while len(self._generations) > self._retained_statuses:
            self._generations.popitem(last=False)
        return CameraWorker(
            definition,
            generation,
            self._event_sink,
            capture_factory=self._capture_factory,
            pipeline_factory=self._pipeline_factory,
            transition_listener=self._on_transition,
        )

    def _on_transition(self, status: CameraStatus) -> None:
        self._remember(status)
        if self._external_transition_listener is not None:
            self._external_transition_listener(status)

    def _remember(self, status: CameraStatus) -> None:
        self._statuses[status.camera_id] = status
        self._statuses.move_to_end(status.camera_id)
        while len(self._statuses) > self._retained_statuses:
            self._statuses.popitem(last=False)

    def _ensure_admission_open(self, camera_id: str | None) -> None:
        if self._admission_closed:
            error = self._shutdown_error or OrchestrationError(
                FailureCategory.SHUTDOWN,
                camera_id,
                "camera manager is shutting down",
            )
            raise error

    @staticmethod
    def _result(
        operation: LifecycleOperation,
        outcome: LifecycleOutcome,
        status: CameraStatus | None,
        *,
        camera_id: str | None = None,
    ) -> LifecycleOperationResult:
        failure = (
            OrchestrationFailure(status.failure_category, status.error_summary)
            if status
            and status.failure_category is not None
            and status.error_summary is not None
            else None
        )
        return LifecycleOperationResult(
            camera_id=status.camera_id if status else camera_id or "",
            operation=operation,
            outcome=outcome,
            generation=status.generation if status else None,
            status=status,
            error=failure,
        )
