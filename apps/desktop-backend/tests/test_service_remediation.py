from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.orchestration import (
    CameraDefinition,
    CameraOrchestrationService,
    CameraWorkerManager,
    OrchestrationError,
    WorkerState,
)

from fakes import CaptureFactory, PipelineFactory, RecordingSink, eventually
from test_remediation_lifecycle import GatedPipelineFactory


class ControlledCatalog:
    def __init__(self, definitions: list[CameraDefinition]) -> None:
        self.definitions = definitions
        self.gate: asyncio.Event | None = None
        self.calls = 0

    async def list_enabled_cameras(self) -> list[CameraDefinition]:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return list(self.definitions)


class ControlledScheduler:
    def __init__(self, *, fail_add: bool = False, fail_remove: bool = False) -> None:
        self.fail_add = fail_add
        self.fail_remove = fail_remove
        self.callback: Any = None
        self.add_calls = 0
        self.remove_calls = 0

    def add_job(self, callback: Any, trigger: str, **kwargs: Any) -> None:
        self.add_calls += 1
        if self.fail_add:
            raise RuntimeError("scheduler registration failed")
        self.callback = callback

    def remove_job(self, job_id: str) -> None:
        self.remove_calls += 1
        if self.fail_remove:
            raise RuntimeError("scheduler shutdown failed")
        self.callback = None


@pytest.mark.asyncio
async def test_scheduler_stop_during_reconcile_waits_for_pending_task() -> None:
    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(),
    )
    catalog = ControlledCatalog([])
    scheduler = ControlledScheduler()
    service = CameraOrchestrationService(manager, catalog, scheduler=scheduler)
    await service.start()
    catalog.gate = asyncio.Event()
    reconcile = asyncio.create_task(service.reconcile())
    await eventually(lambda: catalog.calls == 2)

    stopping = asyncio.create_task(service.stop())
    await asyncio.sleep(0)
    assert not stopping.done()
    catalog.gate.set()
    await asyncio.gather(reconcile, stopping)

    assert manager.active_camera_ids() == ()
    calls_after_stop = catalog.calls
    await service.reconcile()
    assert catalog.calls == calls_after_stop


@pytest.mark.asyncio
async def test_scheduler_callback_racing_stop_cannot_reconcile_after_stop() -> None:
    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(),
    )
    catalog = ControlledCatalog([])
    scheduler = ControlledScheduler()
    service = CameraOrchestrationService(manager, catalog, scheduler=scheduler)
    await service.start()
    callback = scheduler.callback

    await service.stop()
    await callback()

    assert catalog.calls == 1
    assert manager.active_camera_ids() == ()


@pytest.mark.asyncio
async def test_scheduler_start_registration_failure_rolls_back_workers() -> None:
    captures = CaptureFactory()
    pipelines = PipelineFactory()
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=captures, pipeline_factory=pipelines
    )
    service = CameraOrchestrationService(
        manager,
        ControlledCatalog([CameraDefinition("camera", 0)]),
        scheduler=ControlledScheduler(fail_add=True),
    )

    with pytest.raises(OrchestrationError, match="scheduler registration failed"):
        await service.start()

    assert manager.active_camera_ids() == ()
    assert captures.instances[0].close_calls == 1
    assert pipelines.instances[0].aclose_calls == 1
    await service.stop()


@pytest.mark.asyncio
async def test_reconcile_capacity_failure_settles_operations_before_rollback() -> None:
    captures = CaptureFactory()
    pipelines = PipelineFactory()
    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=captures,
        pipeline_factory=pipelines,
        max_active_workers=1,
    )
    service = CameraOrchestrationService(
        manager,
        ControlledCatalog([CameraDefinition("a", 0), CameraDefinition("b", 1)]),
    )

    with pytest.raises(OrchestrationError):
        await service.start()

    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and task.get_name().startswith("camera-")
    ]
    assert manager.active_camera_ids() == ()
    assert pending == []
    assert all(item.close_calls == 1 for item in captures.instances)
    assert all(item.aclose_calls == 1 for item in pipelines.instances)
    await service.stop()


@pytest.mark.asyncio
async def test_scheduler_shutdown_failure_is_reported_after_worker_cleanup() -> None:
    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(),
    )
    service = CameraOrchestrationService(
        manager,
        ControlledCatalog([CameraDefinition("camera", 0)]),
        scheduler=ControlledScheduler(fail_remove=True),
    )
    await service.start()

    with pytest.raises(OrchestrationError, match="service shutdown"):
        await service.stop()

    assert manager.active_camera_ids() == ()
    with pytest.raises(OrchestrationError):
        await service.stop()


@pytest.mark.asyncio
async def test_cancelled_service_stop_is_shared_and_leak_free() -> None:
    gate = asyncio.Event()
    pipelines = GatedPipelineFactory(gate)
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=CaptureFactory(), pipeline_factory=pipelines
    )
    service = CameraOrchestrationService(
        manager,
        ControlledCatalog([CameraDefinition("camera", 0, frame_wait_seconds=0.01)]),
    )
    await service.start()
    await eventually(lambda: pipelines.instances[0].process_calls > 0)

    first = asyncio.create_task(service.stop())
    await eventually(lambda: manager.get_status("camera").state is WorkerState.STOPPING)  # type: ignore[union-attr]
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(service.stop())
    gate.set()
    await second

    assert manager.active_camera_ids() == ()
    assert pipelines.instances[0].aclose_calls == 1


@pytest.mark.asyncio
async def test_repeated_service_start_and_stop_are_idempotent() -> None:
    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(),
    )
    scheduler = ControlledScheduler()
    service = CameraOrchestrationService(
        manager, ControlledCatalog([]), scheduler=scheduler
    )

    await asyncio.gather(service.start(), service.start(), service.start())
    await asyncio.gather(service.stop(), service.stop(), service.stop())

    assert scheduler.add_calls == 1
    assert scheduler.remove_calls == 1
