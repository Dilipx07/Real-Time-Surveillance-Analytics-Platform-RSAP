from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import pytest

from cv_engine import AnalyticsEvent

from app.orchestration import CameraDefinition, CameraWorkerManager, WorkerState

from fakes import CaptureFactory, PipelineFactory, RecordingSink, eventually


@pytest.mark.asyncio
async def test_reconnect_delay_failures_are_backed_off_and_observable() -> None:
    captures = CaptureFactory(outcomes=[False] * 100, read_delay=0)
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=captures, pipeline_factory=PipelineFactory()
    )
    definition = CameraDefinition(
        "offline", "rtsp://camera/live", reconnect_delay_seconds=0.05
    )
    await manager.start_camera(definition)

    await asyncio.sleep(0.16)
    status = manager.get_status("offline")

    assert status is not None and status.state is WorkerState.RECONNECTING
    assert 2 <= captures.instances[0].read_calls <= 5
    assert status.metrics.capture_failures == captures.instances[0].read_calls
    await manager.shutdown()


@pytest.mark.asyncio
async def test_slow_sink_backpressure_queue_is_bounded_and_reports_drops() -> None:
    gate = asyncio.Event()
    sink = RecordingSink(gate=gate)
    manager = CameraWorkerManager(
        sink,
        capture_factory=CaptureFactory(read_delay=0),
        pipeline_factory=PipelineFactory(emit_each_frame=True),
    )
    definition = CameraDefinition(
        "busy", 0, event_queue_size=2, frame_buffer_size=2, frame_wait_seconds=0.005
    )
    await manager.start_camera(definition)

    await eventually(
        lambda: manager.get_status("busy").metrics.events_dropped > 0,  # type: ignore[union-attr]
        timeout=2,
    )
    assert len(manager.get_frame_buffer("busy")) <= 2
    gate.set()
    status = await manager.stop_camera("busy")

    assert status is not None and status.metrics.events_dropped > 0
    assert status.metrics.events_emitted <= sink.calls


@pytest.mark.asyncio
async def test_sink_exception_is_recorded_without_stopping_capture() -> None:
    manager = CameraWorkerManager(
        RecordingSink(fail=True),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(emit_each_frame=True),
    )
    await manager.start_camera(CameraDefinition("camera-1", 0))

    await eventually(
        lambda: manager.get_status("camera-1").metrics.event_sink_failures > 0  # type: ignore[union-attr]
    )
    status = manager.get_status("camera-1")

    assert status is not None and status.state is WorkerState.RUNNING
    assert "secret-value" not in (status.last_error or "")
    assert manager.health().status == "degraded"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_failure_logs_and_status_redact_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manager = CameraWorkerManager(
        RecordingSink(fail=True),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(emit_each_frame=True),
    )
    with caplog.at_level(logging.ERROR):
        await manager.start_camera(CameraDefinition("camera-1", 0))
        await eventually(
            lambda: manager.get_status("camera-1").metrics.event_sink_failures > 0  # type: ignore[union-attr]
        )
        await manager.shutdown()

    assert "secret-value" not in caplog.text
    assert "<redacted>" in caplog.text
    assert "token=" not in caplog.text


@pytest.mark.asyncio
async def test_hung_event_sink_is_timed_out_during_shutdown() -> None:
    gate = asyncio.Event()
    sink = RecordingSink(gate=gate)
    manager = CameraWorkerManager(
        sink,
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(emit_each_frame=True),
    )
    await manager.start_camera(
        CameraDefinition("camera-1", 0, event_sink_timeout_seconds=0.03)
    )
    await eventually(lambda: sink.calls > 0)

    await asyncio.wait_for(manager.shutdown(), timeout=0.5)

    status = manager.get_status("camera-1")
    assert status is not None and status.metrics.event_sink_failures >= 1


@pytest.mark.asyncio
async def test_no_events_are_emitted_after_stop_returns() -> None:
    sink = RecordingSink()
    pipelines = PipelineFactory(emit_each_frame=True)
    manager = CameraWorkerManager(
        sink, capture_factory=CaptureFactory(), pipeline_factory=pipelines
    )
    await manager.start_camera(CameraDefinition("camera-1", 0))
    await eventually(lambda: len(sink.events) >= 2)

    await manager.stop_camera("camera-1")
    count_at_stop = len(sink.events)
    await asyncio.sleep(0.05)

    assert len(sink.events) == count_at_stop
    assert pipelines.instances[0].aclose_calls == 1


@pytest.mark.asyncio
async def test_shutdown_waits_for_inflight_analytics_and_calls_aclose() -> None:
    pipelines = PipelineFactory(process_delay=0.05)
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=CaptureFactory(), pipeline_factory=pipelines
    )
    await manager.start_camera(CameraDefinition("camera-1", 0))
    await eventually(lambda: pipelines.instances[0].process_calls > 0)

    await manager.shutdown()

    assert pipelines.instances[0].closed.is_set()
    assert pipelines.instances[0].aclose_calls == 1
    assert manager.get_status("camera-1").state is WorkerState.STOPPED  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_callback_arriving_during_shutdown_is_dropped() -> None:
    sink = RecordingSink()
    pipelines = PipelineFactory()
    manager = CameraWorkerManager(
        sink, capture_factory=CaptureFactory(), pipeline_factory=pipelines
    )
    await manager.start_camera(CameraDefinition("camera-1", 0))
    pipeline = pipelines.instances[0]

    await manager.stop_camera("camera-1")
    await pipeline.callback(AnalyticsEvent("late", datetime.now(UTC), None, None, {}))

    assert sink.events == []


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_orphan_worker_tasks() -> None:
    pipelines = PipelineFactory(process_delay=0.05)
    manager = CameraWorkerManager(
        RecordingSink(), capture_factory=CaptureFactory(), pipeline_factory=pipelines
    )
    await manager.start_camera(CameraDefinition("camera-1", 0))
    stop_task = asyncio.create_task(manager.stop_camera("camera-1"))
    await asyncio.sleep(0)
    stop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop_task

    status = await manager.stop_camera("camera-1")
    assert status is not None and status.state is WorkerState.STOPPED
    assert pipelines.instances[0].aclose_calls == 1
    await manager.shutdown()
