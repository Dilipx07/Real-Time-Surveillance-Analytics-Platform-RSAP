"""Process-local single-owner camera worker registry."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable

from cv_engine.streaming import FrameBuffer

from .models import ACTIVE_STATES, CameraDefinition, CameraStatus, OrchestrationHealth
from .protocols import CaptureFactory, EventSink, PipelineFactory
from .worker import CameraWorker, default_capture_factory, default_pipeline_factory


class CameraWorkerManager:
    """Serialize lifecycle mutations and enforce one worker per camera."""

    def __init__(
        self,
        event_sink: EventSink,
        *,
        capture_factory: CaptureFactory = default_capture_factory,
        pipeline_factory: PipelineFactory = default_pipeline_factory,
        retained_statuses: int = 256,
        transition_listener: Callable[[CameraStatus], None] | None = None,
    ) -> None:
        if retained_statuses < 1:
            raise ValueError("retained_statuses must be positive")
        self._event_sink = event_sink
        self._capture_factory = capture_factory
        self._pipeline_factory = pipeline_factory
        self._retained_statuses = retained_statuses
        self._external_transition_listener = transition_listener
        self._workers: dict[str, CameraWorker] = {}
        self._statuses: OrderedDict[str, CameraStatus] = OrderedDict()
        self._generations: OrderedDict[str, int] = OrderedDict()
        self._lifecycle_lock = asyncio.Lock()
        self._closed = False

    async def start_camera(self, definition: CameraDefinition) -> CameraStatus:
        async with self._lifecycle_lock:
            self._ensure_open()
            current = self._workers.get(definition.camera_id)
            if current is not None and current.state in ACTIVE_STATES:
                return current.status
            if current is not None:
                await current.stop()
                self._remember(current.status)
                self._workers.pop(definition.camera_id, None)

            worker = self._new_worker(definition)
            self._workers[definition.camera_id] = worker
            try:
                status = await worker.start()
            except BaseException:
                await worker.wait()
                self._remember(worker.status)
                self._workers.pop(definition.camera_id, None)
                raise
            self._remember(status)
            return status

    async def stop_camera(self, camera_id: str) -> CameraStatus | None:
        async with self._lifecycle_lock:
            worker = self._workers.get(camera_id)
            if worker is None:
                return self._statuses.get(camera_id)
            status = await worker.stop()
            self._remember(status)
            self._workers.pop(camera_id, None)
            return status

    async def restart_camera(self, definition: CameraDefinition) -> CameraStatus:
        async with self._lifecycle_lock:
            self._ensure_open()
            current = self._workers.pop(definition.camera_id, None)
            if current is not None:
                self._remember(await current.stop())
            worker = self._new_worker(definition)
            self._workers[definition.camera_id] = worker
            try:
                status = await worker.start()
            except BaseException:
                await worker.wait()
                self._remember(worker.status)
                self._workers.pop(definition.camera_id, None)
                raise
            self._remember(status)
            return status

    async def reconcile(
        self, desired: list[CameraDefinition]
    ) -> dict[str, CameraStatus]:
        desired_by_id = {item.camera_id: item for item in desired}
        if len(desired_by_id) != len(desired):
            raise ValueError("camera catalog returned duplicate camera IDs")
        async with self._lifecycle_lock:
            self._ensure_open()
            for camera_id in tuple(self._workers):
                if camera_id not in desired_by_id:
                    worker = self._workers.pop(camera_id)
                    self._remember(await worker.stop())
            for camera_id, definition in desired_by_id.items():
                current = self._workers.get(camera_id)
                if (
                    current is not None
                    and current.definition == definition
                    and current.state in ACTIVE_STATES
                ):
                    continue
                if current is not None:
                    self._workers.pop(camera_id)
                    self._remember(await current.stop())
                worker = self._new_worker(definition)
                self._workers[camera_id] = worker
                try:
                    self._remember(await worker.start())
                except BaseException:
                    await worker.wait()
                    self._remember(worker.status)
                    self._workers.pop(camera_id, None)
                    raise
            return self.statuses()

    def get_frame_buffer(self, camera_id: str) -> FrameBuffer:
        worker = self._workers.get(camera_id)
        if worker is None or worker.state not in ACTIVE_STATES:
            raise KeyError(f"camera is not active: {camera_id}")
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
            if worker.state in ACTIVE_STATES
        )

    def health(self) -> OrchestrationHealth:
        return OrchestrationHealth.from_statuses(self.statuses())

    async def shutdown(self) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            workers = tuple(self._workers.values())
            self._workers.clear()
            results = await asyncio.gather(
                *(worker.stop() for worker in workers), return_exceptions=True
            )
            for worker, result in zip(workers, results, strict=True):
                self._remember(worker.status)
                if isinstance(result, asyncio.CancelledError):
                    raise result

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

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("camera worker manager is shut down")
