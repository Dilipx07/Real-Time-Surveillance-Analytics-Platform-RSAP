from __future__ import annotations

import asyncio

import pytest

from app.orchestration import CameraDefinition, CameraWorkerManager, WorkerState

from fakes import CaptureFactory, PipelineFactory, RecordingSink, eventually


@pytest.mark.asyncio
async def test_concurrent_start_creates_exactly_one_worker() -> None:
    captures = CaptureFactory()
    pipelines = PipelineFactory()
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=captures, pipeline_factory=pipelines
    )
    definition = CameraDefinition("camera-1", "rtsp://user:secret@camera/live")

    statuses = await asyncio.gather(
        *(manager.start_camera(definition) for _ in range(50))
    )

    assert {status.generation for status in statuses} == {1}
    assert len(captures.instances) == len(pipelines.instances) == 1
    assert manager.active_camera_ids() == ("camera-1",)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_stop_is_idempotent_and_restart_waits_for_old_worker() -> None:
    captures = CaptureFactory()
    pipelines = PipelineFactory()
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=captures, pipeline_factory=pipelines
    )
    definition = CameraDefinition("camera-1", 0)
    first = await manager.start_camera(definition)

    stopped = await asyncio.gather(
        *(manager.stop_camera("camera-1") for _ in range(20))
    )
    restarted = await manager.restart_camera(definition)

    assert first.generation == 1
    assert all(
        item is not None and item.state is WorkerState.STOPPED for item in stopped
    )
    assert restarted.state is WorkerState.RUNNING
    assert restarted.generation == 2
    assert captures.instances[0].closed.is_set()
    assert pipelines.instances[0].aclose_calls == 1
    await manager.shutdown()
    assert pipelines.instances[1].aclose_calls == 1


@pytest.mark.asyncio
async def test_startup_failure_leaves_no_active_worker_and_closes_capture() -> None:
    captures = CaptureFactory()

    def broken_pipeline(*args: object) -> object:
        raise RuntimeError("cannot load rtsp://admin:hunter2@camera/live")

    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=captures,
        pipeline_factory=broken_pipeline,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="cannot load"):
        await manager.start_camera(CameraDefinition("bad-camera", "secret-source"))

    status = manager.get_status("bad-camera")
    assert status is not None
    assert status.state is WorkerState.FAILED
    assert "hunter2" not in (status.last_error or "")
    assert manager.active_camera_ids() == ()
    assert captures.instances[0].closed.is_set()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_runtime_exception_is_observable_and_pipeline_is_closed() -> None:
    captures = CaptureFactory()
    pipelines = PipelineFactory(fail_after=1)
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=captures, pipeline_factory=pipelines
    )
    await manager.start_camera(CameraDefinition("camera-1", 0))

    await eventually(lambda: manager.get_status("camera-1").state is WorkerState.FAILED)  # type: ignore[union-attr]
    status = manager.get_status("camera-1")

    assert status is not None and status.metrics.worker_failures >= 1
    assert "password" not in (status.last_error or "")
    assert pipelines.instances[0].aclose_calls == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_repeated_start_stop_races_never_duplicate_workers() -> None:
    captures = CaptureFactory()
    pipelines = PipelineFactory()
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=captures, pipeline_factory=pipelines
    )
    definition = CameraDefinition("camera-race", 0, frame_wait_seconds=0.01)

    for _ in range(30):
        await asyncio.gather(
            manager.start_camera(definition),
            manager.start_camera(definition),
            manager.stop_camera(definition.camera_id),
        )
        assert len(manager.active_camera_ids()) <= 1
        await manager.stop_camera(definition.camera_id)

    assert all(pipeline.aclose_calls == 1 for pipeline in pipelines.instances)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_status_retention_is_bounded() -> None:
    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(),
        retained_statuses=3,
    )
    for index in range(8):
        definition = CameraDefinition(f"camera-{index}", index)
        await manager.start_camera(definition)
        await manager.stop_camera(definition.camera_id)

    assert len(manager.statuses()) == 3
    await manager.shutdown()
