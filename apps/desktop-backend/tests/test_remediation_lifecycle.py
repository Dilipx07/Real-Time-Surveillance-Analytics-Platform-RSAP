from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from app.orchestration import (
    CameraDefinition,
    CameraWorkerManager,
    LifecycleOutcome,
    OrchestrationError,
    ShutdownError,
    WorkerState,
)
from app.orchestration.models import VALID_TRANSITIONS
from app.orchestration.worker import CameraWorker

from fakes import (
    CaptureFactory,
    FakeCapture,
    FakePipeline,
    PipelineFactory,
    RecordingSink,
    eventually,
)


class GatedPipeline(FakePipeline):
    def __init__(self, callback: Any, gate: asyncio.Event) -> None:
        super().__init__(callback)
        self.gate = gate

    async def process_if_due(self, frame: Any, timestamp: Any) -> None:
        self.process_calls += 1
        await self.gate.wait()


class GatedPipelineFactory:
    def __init__(self, gate: asyncio.Event, gated_camera: str | None = None) -> None:
        self.gate = gate
        self.gated_camera = gated_camera
        self.instances: list[FakePipeline] = []

    def __call__(self, definition: CameraDefinition, callback: Any) -> FakePipeline:
        pipeline: FakePipeline
        if self.gated_camera is None or definition.camera_id == self.gated_camera:
            pipeline = GatedPipeline(callback, self.gate)
        else:
            pipeline = FakePipeline(callback)
        self.instances.append(pipeline)
        return pipeline


@pytest.mark.asyncio
async def test_cancelled_shutdown_second_waiter_completes_shared_shutdown() -> None:
    gate = asyncio.Event()
    pipelines = GatedPipelineFactory(gate)
    captures = CaptureFactory()
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=captures, pipeline_factory=pipelines
    )
    await manager.start_camera(CameraDefinition("camera", 0, frame_wait_seconds=0.01))
    await eventually(lambda: pipelines.instances[0].process_calls > 0)

    first = asyncio.create_task(manager.shutdown())
    await eventually(lambda: manager.get_status("camera").state is WorkerState.STOPPING)  # type: ignore[union-attr]
    second = asyncio.create_task(manager.shutdown())
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    gate.set()
    await second

    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and task.get_name().startswith("camera-")
    ]
    assert pending == []
    assert manager.active_camera_ids() == ()
    assert pipelines.instances[0].aclose_calls == 1
    assert captures.instances[0].close_calls == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_pipeline_close_failure_is_aggregated_without_task_leak() -> None:
    class CloseFailPipeline(FakePipeline):
        async def aclose(self, *, cancel_pending: bool = False) -> None:
            self.aclose_calls += 1
            self.closed.set()
            raise RuntimeError("pipeline close failed")

    pipelines: list[CloseFailPipeline] = []

    def factory(definition: CameraDefinition, callback: Any) -> CloseFailPipeline:
        pipeline = CloseFailPipeline(callback)
        pipelines.append(pipeline)
        return pipeline

    captures = CaptureFactory()
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=captures, pipeline_factory=factory
    )
    await manager.start_camera(CameraDefinition("camera", 0))

    with pytest.raises(ShutdownError) as first:
        await manager.shutdown()
    with pytest.raises(ShutdownError) as second:
        await manager.shutdown()

    assert first.value is second.value
    assert manager.active_camera_ids() == ()
    assert pipelines[0].aclose_calls == 1
    assert captures.instances[0].close_calls == 1


@pytest.mark.asyncio
async def test_capture_close_once_and_shutdown_exception_aggregation() -> None:
    class CloseFailCapture(FakeCapture):
        def close(self) -> None:
            self.close_calls += 1
            self.closed.set()
            raise RuntimeError("capture close failed")

    captures: list[CloseFailCapture] = []

    def factory(definition: CameraDefinition) -> CloseFailCapture:
        capture = CloseFailCapture()
        captures.append(capture)
        return capture

    pipelines = PipelineFactory()
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=factory, pipeline_factory=pipelines
    )
    await manager.start_camera(CameraDefinition("camera", 0))

    with pytest.raises(ShutdownError):
        await manager.shutdown()

    assert captures[0].close_calls == 1
    assert pipelines.instances[0].aclose_calls == 1
    assert manager.active_camera_ids() == ()


@pytest.mark.asyncio
async def test_start_during_stop_after_caller_cancellation_starts_one_replacement() -> (
    None
):
    gate = asyncio.Event()
    pipelines = GatedPipelineFactory(gate)
    captures = CaptureFactory()
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=captures, pipeline_factory=pipelines
    )
    definition = CameraDefinition("camera", 0, frame_wait_seconds=0.01)
    await manager.start_camera(definition)
    await eventually(lambda: pipelines.instances[0].process_calls > 0)

    stop = asyncio.create_task(manager.stop_camera("camera"))
    await eventually(lambda: manager.get_status("camera").state is WorkerState.STOPPING)  # type: ignore[union-attr]
    stop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop
    starts = [
        asyncio.create_task(manager.start_camera(definition)),
        asyncio.create_task(manager.start_camera(definition)),
    ]
    gate.set()
    results = await asyncio.gather(*starts)

    assert {item.outcome for item in results} == {
        LifecycleOutcome.STARTED,
        LifecycleOutcome.ALREADY_RUNNING,
    }
    assert {item.generation for item in results} == {2}
    assert manager.active_camera_ids() == ("camera",)
    assert len(captures.instances) == 2
    await manager.shutdown()


@pytest.mark.asyncio
async def test_restart_during_stop_and_stop_during_restart_are_serialized() -> None:
    gate = asyncio.Event()
    pipelines = GatedPipelineFactory(gate)
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=CaptureFactory(), pipeline_factory=pipelines
    )
    definition = CameraDefinition("camera", 0, frame_wait_seconds=0.01)
    await manager.start_camera(definition)
    await eventually(lambda: pipelines.instances[0].process_calls > 0)
    stopping = asyncio.create_task(manager.stop_camera("camera"))
    await eventually(lambda: manager.get_status("camera").state is WorkerState.STOPPING)  # type: ignore[union-attr]
    stopping.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stopping

    restarting = asyncio.create_task(manager.restart_camera(definition))
    stopping_replacement = asyncio.create_task(manager.stop_camera("camera"))
    gate.set()
    restart_result, stop_result = await asyncio.gather(restarting, stopping_replacement)

    assert restart_result.outcome is LifecycleOutcome.RESTARTED
    assert restart_result.generation == 2
    assert stop_result.outcome is LifecycleOutcome.STOPPED
    assert manager.active_camera_ids() == ()
    await manager.shutdown()


@pytest.mark.asyncio
async def test_camera_b_started_while_camera_a_blocked_in_startup() -> None:
    entered = threading.Event()
    release = threading.Event()

    def pipeline_factory(definition: CameraDefinition, callback: Any) -> FakePipeline:
        if definition.camera_id == "a":
            entered.set()
            release.wait(timeout=2)
        return FakePipeline(callback)

    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=pipeline_factory,
    )
    camera_a = asyncio.create_task(manager.start_camera(CameraDefinition("a", 0)))
    await asyncio.to_thread(entered.wait, 1)

    camera_b = await asyncio.wait_for(
        manager.start_camera(CameraDefinition("b", 1)), timeout=0.5
    )
    release.set()
    await camera_a

    assert camera_b.outcome is LifecycleOutcome.STARTED
    assert set(manager.active_camera_ids()) == {"a", "b"}
    await manager.shutdown()


@pytest.mark.asyncio
async def test_shutdown_racing_blocked_start_wins_without_leak() -> None:
    entered = threading.Event()
    release = threading.Event()
    captures = CaptureFactory()
    pipelines: list[FakePipeline] = []

    def pipeline_factory(definition: CameraDefinition, callback: Any) -> FakePipeline:
        entered.set()
        release.wait(timeout=2)
        pipeline = FakePipeline(callback)
        pipelines.append(pipeline)
        return pipeline

    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=captures, pipeline_factory=pipeline_factory
    )
    starting = asyncio.create_task(manager.start_camera(CameraDefinition("camera", 0)))
    await asyncio.to_thread(entered.wait, 1)
    shutdown = asyncio.create_task(manager.shutdown())
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(OrchestrationError) as start_error:
        await starting
    await shutdown

    assert start_error.value.category.value == "shutdown"
    assert manager.active_camera_ids() == ()
    assert captures.instances[0].close_calls == 1
    assert pipelines[0].aclose_calls == 1


@pytest.mark.asyncio
async def test_camera_b_restarts_while_camera_a_blocked_in_stop() -> None:
    gate = asyncio.Event()
    pipelines = GatedPipelineFactory(gate, gated_camera="a")
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=CaptureFactory(), pipeline_factory=pipelines
    )
    camera_a = CameraDefinition("a", 0, frame_wait_seconds=0.01)
    camera_b = CameraDefinition("b", 1, frame_wait_seconds=0.01)
    await asyncio.gather(manager.start_camera(camera_a), manager.start_camera(camera_b))
    gated = next(
        item for item in pipelines.instances if isinstance(item, GatedPipeline)
    )
    await eventually(lambda: gated.process_calls > 0)

    stopping_a = asyncio.create_task(manager.stop_camera("a"))
    await eventually(lambda: manager.get_status("a").state is WorkerState.STOPPING)  # type: ignore[union-attr]
    restarted_b = await asyncio.wait_for(manager.restart_camera(camera_b), timeout=0.5)
    assert restarted_b.outcome is LifecycleOutcome.RESTARTED
    gate.set()
    await stopping_a
    await manager.shutdown()


@pytest.mark.asyncio
async def test_blocked_capture_stop_does_not_block_other_camera_lifecycle() -> None:
    entered = threading.Event()
    release = threading.Event()
    captures: dict[str, FakeCapture] = {}

    class BlockingCapture(FakeCapture):
        def read(self) -> tuple[bool, Any]:
            entered.set()
            release.wait(timeout=2)
            return super().read()

    def capture_factory(definition: CameraDefinition) -> FakeCapture:
        capture: FakeCapture = (
            BlockingCapture(read_delay=0)
            if definition.camera_id == "a"
            else FakeCapture()
        )
        captures[definition.camera_id] = capture
        return capture

    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=capture_factory,
        pipeline_factory=PipelineFactory(),
    )
    camera_a = CameraDefinition("a", 0)
    camera_b = CameraDefinition("b", 1)
    await asyncio.gather(manager.start_camera(camera_a), manager.start_camera(camera_b))
    await asyncio.to_thread(entered.wait, 1)

    stop_a = asyncio.create_task(manager.stop_camera("a"))
    await eventually(lambda: manager.get_status("a").state is WorkerState.STOPPING)  # type: ignore[union-attr]
    restart_b = await asyncio.wait_for(manager.restart_camera(camera_b), timeout=0.5)
    assert restart_b.outcome is LifecycleOutcome.RESTARTED
    assert not stop_a.done()
    release.set()
    await stop_a
    assert captures["a"].close_calls == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_worker_capacity_concurrent_final_slot_is_bounded() -> None:
    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(),
        max_active_workers=1,
    )
    results = await asyncio.gather(
        manager.start_camera(CameraDefinition("a", 0)),
        manager.start_camera(CameraDefinition("b", 1)),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, BaseException) for item in results) == 1
    error = next(item for item in results if isinstance(item, OrchestrationError))
    assert error.category.value == "capacity"
    assert len(manager.active_camera_ids()) == 1
    await manager.shutdown()


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (source, target)
        for source, targets in VALID_TRANSITIONS.items()
        for target in targets
    ],
)
def test_lifecycle_valid_transition_table(
    source: WorkerState, target: WorkerState
) -> None:
    worker = CameraWorker(
        CameraDefinition("camera", 0),
        1,
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(),
    )
    worker._state = source
    worker._transition(target)
    assert worker.state is target


def test_lifecycle_invalid_transition_is_rejected() -> None:
    worker = CameraWorker(
        CameraDefinition("camera", 0),
        1,
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(),
    )
    with pytest.raises(OrchestrationError, match="invalid lifecycle transition"):
        worker._transition(WorkerState.RUNNING)
