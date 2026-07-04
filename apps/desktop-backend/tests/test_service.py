from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.orchestration import (
    CameraDefinition,
    CameraOrchestrationService,
    CameraWorkerManager,
    OrchestrationError,
)

from fakes import CaptureFactory, PipelineFactory, RecordingSink


class Catalog:
    def __init__(self, definitions: list[CameraDefinition]) -> None:
        self.definitions = definitions
        self.calls = 0
        self.gate: asyncio.Event | None = None

    async def list_enabled_cameras(self) -> list[CameraDefinition]:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return list(self.definitions)


class FakeScheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, tuple[Any, str, dict[str, Any]]] = {}
        self.removed: list[str] = []

    def add_job(self, func: Any, trigger: str, **kwargs: Any) -> None:
        self.jobs[kwargs["id"]] = (func, trigger, kwargs)

    def remove_job(self, job_id: str) -> None:
        self.removed.append(job_id)
        self.jobs.pop(job_id, None)


@pytest.mark.asyncio
async def test_service_reconciles_catalog_and_registers_single_scheduler_job() -> None:
    captures = CaptureFactory()
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=captures, pipeline_factory=PipelineFactory()
    )
    catalog = Catalog([CameraDefinition("one", 0), CameraDefinition("two", 1)])
    scheduler = FakeScheduler()
    service = CameraOrchestrationService(manager, catalog, scheduler=scheduler)

    await service.start()
    await service.start()

    assert set(manager.active_camera_ids()) == {"one", "two"}
    assert len(captures.instances) == 2
    assert set(scheduler.jobs) == {service.JOB_ID}
    catalog.definitions = [CameraDefinition("two", 1)]
    await service.reconcile()
    assert manager.active_camera_ids() == ("two",)
    await service.stop()
    assert scheduler.removed == [service.JOB_ID]


@pytest.mark.asyncio
async def test_overlapping_reconcile_is_coalesced() -> None:
    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(),
    )
    catalog = Catalog([])
    service = CameraOrchestrationService(manager, catalog)
    await service.start()
    catalog.gate = asyncio.Event()

    first = asyncio.create_task(service.reconcile())
    await asyncio.sleep(0)
    await service.reconcile()
    catalog.gate.set()
    await first

    assert catalog.calls == 2
    await service.stop()


@pytest.mark.asyncio
async def test_duplicate_catalog_ids_fail_without_starting_workers() -> None:
    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(),
    )
    catalog = Catalog([CameraDefinition("same", 0), CameraDefinition("same", 1)])
    service = CameraOrchestrationService(manager, catalog)

    with pytest.raises(OrchestrationError, match="duplicate"):
        await service.start()

    assert manager.active_camera_ids() == ()
    await service.stop()
