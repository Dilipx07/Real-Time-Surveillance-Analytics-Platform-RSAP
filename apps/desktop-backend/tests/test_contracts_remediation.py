from __future__ import annotations

import inspect
import json
import logging
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from cv_engine import AnalyticsEvent

from app.orchestration import (
    CameraDefinition,
    CameraHealth,
    CameraStatus,
    CameraWorkerManager,
    FailureCategory,
    LifecycleOutcome,
    OrchestrationError,
    RoutedAnalyticsEvent,
    WorkerState,
)
from app.orchestration import protocols
from app.orchestration.models import MIN_RECONNECT_DELAY_SECONDS

from fakes import CaptureFactory, PipelineFactory, RecordingSink


@pytest.mark.parametrize(
    "value", [0.0, -0.01, float("nan"), float("inf"), float("-inf")]
)
def test_zero_delay_and_non_finite_reconnect_delay_are_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="reconnect_delay_seconds"):
        CameraDefinition("camera", 0, reconnect_delay_seconds=value)


def test_reconnect_delay_minimum_boundary_is_valid_and_bounded() -> None:
    definition = CameraDefinition(
        "camera", 0, reconnect_delay_seconds=MIN_RECONNECT_DELAY_SECONDS
    )
    assert definition.reconnect_delay_seconds == MIN_RECONNECT_DELAY_SECONDS
    with pytest.raises(ValueError):
        CameraDefinition("camera", 0, reconnect_delay_seconds=301)


@pytest.mark.parametrize("field", ["frame_wait_seconds", "event_sink_timeout_seconds"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_all_orchestration_float_settings_reject_non_finite_values(
    field: str, value: float
) -> None:
    with pytest.raises(ValueError):
        CameraDefinition("camera", 0, **{field: value})


@pytest.mark.asyncio
async def test_agent4_status_dto_json_serialization_and_round_trip() -> None:
    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=PipelineFactory(),
    )
    operation = await manager.start_camera(CameraDefinition("camera", 0))
    status = manager.get_status("camera")
    assert status is not None

    required = {
        "camera_id",
        "generation",
        "lifecycle_state",
        "health",
        "is_running",
        "last_frame_at",
        "last_event_at",
        "failure_category",
        "error_summary",
        "reconnect_count",
        "processing_fps",
        "frame_buffer_size",
        "event_queue_size",
        "callback_backlog",
        "dropped_event_count",
    }
    encoded = status.to_dict()
    assert required <= encoded.keys()
    assert json.loads(json.dumps(encoded))["camera_id"] == "camera"
    assert CameraStatus.from_dict(encoded) == status
    assert json.loads(json.dumps(operation.to_dict()))["outcome"] == "started"
    assert operation.outcome is LifecycleOutcome.STARTED
    assert json.loads(json.dumps(manager.health().to_dict()))["status"] == "ok"
    with pytest.raises(FrozenInstanceError):
        status.camera_id = "changed"  # type: ignore[misc]
    await manager.shutdown()


@pytest.mark.parametrize("state", list(WorkerState))
def test_agent4_status_dto_serializes_every_lifecycle_state(state: WorkerState) -> None:
    now = datetime.now(UTC)
    status = CameraStatus(
        camera_id="camera",
        generation=1,
        lifecycle_state=state,
        health=CameraHealth.FAILED if state is WorkerState.FAILED else CameraHealth.OK,
        is_running=state in {WorkerState.RUNNING, WorkerState.RECONNECTING},
        updated_at=now,
        last_frame_at=now,
        last_event_at=now,
        last_processed_at=now,
        failure_category=FailureCategory.PIPELINE
        if state is WorkerState.FAILED
        else None,
        error_summary="redacted failure" if state is WorkerState.FAILED else None,
        reconnect_count=1,
        processing_fps=4.0,
        frame_buffer_size=1,
        frame_buffer_capacity=10,
        event_queue_size=0,
        event_queue_capacity=100,
        callback_backlog=0,
        dropped_event_count=0,
        frames_captured=1,
        frames_processed=1,
        capture_failures=0,
        events_emitted=0,
        event_sink_failures=0,
        worker_failures=0,
        transition_count=2,
    )
    assert CameraStatus.from_dict(json.loads(json.dumps(status.to_dict()))) == status


def test_immutable_event_dto_deep_copies_and_freezes_payload() -> None:
    original = {"nested": {"values": [1, 2]}, "label": "person"}
    callback = AnalyticsEvent("zone_enter", datetime.now(UTC), 7, "zone-a", original)
    routed = RoutedAnalyticsEvent.from_callback("camera", 3, callback)
    original["nested"]["values"].append(3)  # type: ignore[index,union-attr]
    exported = routed.to_dict()
    exported["payload"]["nested"]["values"].append(4)

    assert routed.payload.to_dict() == {
        "nested": {"values": [1, 2]},
        "label": "person",
    }
    with pytest.raises(TypeError):
        routed.payload["label"] = "changed"  # type: ignore[index]
    assert json.loads(json.dumps(routed.to_dict()))["generation"] == 3


@pytest.mark.parametrize(
    "event",
    [
        AnalyticsEvent("test", datetime.now(), None, None, {}),
        AnalyticsEvent("test", datetime.now(UTC), None, None, {"bad": {1, 2}}),
        AnalyticsEvent("test", datetime.now(UTC), None, None, {"bad": float("nan")}),
        AnalyticsEvent(
            "test",
            datetime.now(UTC),
            None,
            None,
            {"stream_url": "rtsp://example.invalid/stream"},
        ),
    ],
)
def test_event_dto_rejects_invalid_timestamp_or_payload(event: AnalyticsEvent) -> None:
    with pytest.raises(OrchestrationError) as failure:
        RoutedAnalyticsEvent.from_callback("camera", 1, event)
    assert failure.value.category is FailureCategory.CALLBACK


def test_agent2_protocol_has_no_cv_engine_event_dependency() -> None:
    assert "cv_engine" not in inspect.getsource(protocols)


@pytest.mark.asyncio
async def test_sanitized_exception_status_logs_and_dto_hide_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sources = (
        "rtsp://admin:secret@example.local:554/stream?token=hidden",
        "http://user:password@example.local/path?api_key=hidden",
    )

    def broken_pipeline(*args: object) -> object:
        raise RuntimeError(f"model startup failed for {sources[0]} and {sources[1]}")

    manager = CameraWorkerManager(
        RecordingSink(),
        capture_factory=CaptureFactory(),
        pipeline_factory=broken_pipeline,  # type: ignore[arg-type]
    )
    with caplog.at_level(logging.ERROR):
        with pytest.raises(OrchestrationError) as failure:
            await manager.start_camera(CameraDefinition("camera", sources[0]))

    status = manager.get_status("camera")
    assert status is not None
    public_text = " ".join(
        [
            str(failure.value),
            repr(failure.value),
            caplog.text,
            json.dumps(status.to_dict()),
        ]
    ).lower()
    for forbidden in ("admin", "secret", "password", "hidden", "token=", "api_key="):
        assert forbidden not in public_text
    assert failure.value.category is FailureCategory.MODEL
    await manager.shutdown()
