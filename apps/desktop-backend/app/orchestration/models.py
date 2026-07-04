"""Immutable, serializable orchestration state and boundary contracts."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from pathlib import Path
from typing import Any, TypeAlias

from cv_engine import CVConfig

from .errors import FailureCategory, OrchestrationError, OrchestrationFailure

MIN_RECONNECT_DELAY_SECONDS = 0.05
MAX_RECONNECT_DELAY_SECONDS = 300.0
MAX_WAIT_SECONDS = 300.0
_SENSITIVE_EVENT_KEYS = frozenset(
    {
        "source",
        "stream_url",
        "rtsp_url",
        "camera_url",
        "password",
        "token",
        "secret",
        "credential",
        "api_key",
        "authorization",
        "aadhaar",
    }
)


class WorkerState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    FAILED = "failed"


class CameraHealth(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    FAILED = "failed"


class LifecycleOperation(StrEnum):
    START = "start"
    STOP = "stop"
    RESTART = "restart"


class LifecycleOutcome(StrEnum):
    STARTED = "started"
    ALREADY_RUNNING = "already_running"
    STOPPED = "stopped"
    ALREADY_STOPPED = "already_stopped"
    RESTARTED = "restarted"
    FAILED = "failed"


ACTIVE_STATES = frozenset(
    {
        WorkerState.STARTING,
        WorkerState.RUNNING,
        WorkerState.RECONNECTING,
        WorkerState.STOPPING,
    }
)
RUNNING_STATES = frozenset({WorkerState.RUNNING, WorkerState.RECONNECTING})

VALID_TRANSITIONS: Mapping[WorkerState, frozenset[WorkerState]] = {
    WorkerState.STOPPED: frozenset({WorkerState.STARTING}),
    WorkerState.STARTING: frozenset(
        {
            WorkerState.RUNNING,
            WorkerState.RECONNECTING,
            WorkerState.FAILED,
            WorkerState.STOPPING,
        }
    ),
    WorkerState.RUNNING: frozenset(
        {WorkerState.RECONNECTING, WorkerState.STOPPING, WorkerState.FAILED}
    ),
    WorkerState.RECONNECTING: frozenset(
        {WorkerState.RUNNING, WorkerState.STOPPING, WorkerState.FAILED}
    ),
    WorkerState.STOPPING: frozenset({WorkerState.STOPPED, WorkerState.FAILED}),
    WorkerState.FAILED: frozenset({WorkerState.STOPPING, WorkerState.STOPPED}),
}


def _aware_utc(value: object, name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise OrchestrationError(
            FailureCategory.CALLBACK, None, f"{name} must be timezone-aware"
        )
    return value.astimezone(UTC)


def _validate_finite(value: float, name: str, minimum: float, maximum: float) -> None:
    if not isfinite(value) or value < minimum or value > maximum:
        raise ValueError(f"{name} must be finite and between {minimum} and {maximum}")


@dataclass(frozen=True, slots=True)
class CameraDefinition:
    """One desired camera configuration; the connection source is never represented."""

    camera_id: str
    source: str | int | Path = field(repr=False)
    cv_config: CVConfig = field(default_factory=CVConfig)
    frame_buffer_size: int = 10
    event_queue_size: int = 100
    event_sink_timeout_seconds: float = 5.0
    reconnect_delay_seconds: float = 2.0
    frame_wait_seconds: float = 0.25

    def __post_init__(self) -> None:
        if not self.camera_id.strip():
            raise ValueError("camera_id must not be empty")
        if self.frame_buffer_size < 1 or self.event_queue_size < 1:
            raise ValueError("buffer and event queue sizes must be positive")
        _validate_finite(
            self.reconnect_delay_seconds,
            "reconnect_delay_seconds",
            MIN_RECONNECT_DELAY_SECONDS,
            MAX_RECONNECT_DELAY_SECONDS,
        )
        _validate_finite(
            self.frame_wait_seconds, "frame_wait_seconds", 0.001, MAX_WAIT_SECONDS
        )
        _validate_finite(
            self.event_sink_timeout_seconds,
            "event_sink_timeout_seconds",
            0.001,
            MAX_WAIT_SECONDS,
        )


@dataclass(frozen=True, slots=True)
class CameraMetrics:
    frames_captured: int = 0
    frames_processed: int = 0
    capture_failures: int = 0
    events_emitted: int = 0
    events_dropped: int = 0
    event_sink_failures: int = 0
    worker_failures: int = 0
    reconnect_count: int = 0
    processing_fps: float = 0.0
    last_frame_at: datetime | None = None
    last_processed_at: datetime | None = None
    last_event_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class StateTransition:
    previous: WorkerState
    current: WorkerState
    timestamp: datetime
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CameraStatus:
    camera_id: str
    generation: int
    lifecycle_state: WorkerState
    health: CameraHealth
    is_running: bool
    updated_at: datetime
    last_frame_at: datetime | None
    last_event_at: datetime | None
    last_processed_at: datetime | None
    failure_category: FailureCategory | None
    error_summary: str | None
    reconnect_count: int
    processing_fps: float
    frame_buffer_size: int
    frame_buffer_capacity: int
    event_queue_size: int
    event_queue_capacity: int
    callback_backlog: int
    dropped_event_count: int
    frames_captured: int
    frames_processed: int
    capture_failures: int
    events_emitted: int
    event_sink_failures: int
    worker_failures: int
    transition_count: int

    @property
    def state(self) -> WorkerState:
        return self.lifecycle_state

    @property
    def last_error(self) -> str | None:
        return self.error_summary

    @property
    def metrics(self) -> CameraMetrics:
        return CameraMetrics(
            frames_captured=self.frames_captured,
            frames_processed=self.frames_processed,
            capture_failures=self.capture_failures,
            events_emitted=self.events_emitted,
            events_dropped=self.dropped_event_count,
            event_sink_failures=self.event_sink_failures,
            worker_failures=self.worker_failures,
            reconnect_count=self.reconnect_count,
            processing_fps=self.processing_fps,
            last_frame_at=self.last_frame_at,
            last_processed_at=self.last_processed_at,
            last_event_at=self.last_event_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "generation": self.generation,
            "lifecycle_state": self.lifecycle_state.value,
            "health": self.health.value,
            "is_running": self.is_running,
            "updated_at": self.updated_at.isoformat(),
            "last_frame_at": self.last_frame_at.isoformat()
            if self.last_frame_at
            else None,
            "last_event_at": self.last_event_at.isoformat()
            if self.last_event_at
            else None,
            "last_processed_at": self.last_processed_at.isoformat()
            if self.last_processed_at
            else None,
            "failure_category": self.failure_category.value
            if self.failure_category
            else None,
            "error_summary": self.error_summary,
            "reconnect_count": self.reconnect_count,
            "processing_fps": self.processing_fps,
            "frame_buffer_size": self.frame_buffer_size,
            "frame_buffer_capacity": self.frame_buffer_capacity,
            "event_queue_size": self.event_queue_size,
            "event_queue_capacity": self.event_queue_capacity,
            "callback_backlog": self.callback_backlog,
            "dropped_event_count": self.dropped_event_count,
            "frames_captured": self.frames_captured,
            "frames_processed": self.frames_processed,
            "capture_failures": self.capture_failures,
            "events_emitted": self.events_emitted,
            "event_sink_failures": self.event_sink_failures,
            "worker_failures": self.worker_failures,
            "transition_count": self.transition_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> CameraStatus:
        def parsed(name: str) -> datetime | None:
            raw = value.get(name)
            return _aware_utc(datetime.fromisoformat(raw), name) if raw else None

        return cls(
            camera_id=str(value["camera_id"]),
            generation=int(value["generation"]),
            lifecycle_state=WorkerState(value["lifecycle_state"]),
            health=CameraHealth(value["health"]),
            is_running=bool(value["is_running"]),
            updated_at=_aware_utc(
                datetime.fromisoformat(value["updated_at"]), "updated_at"
            ),
            last_frame_at=parsed("last_frame_at"),
            last_event_at=parsed("last_event_at"),
            last_processed_at=parsed("last_processed_at"),
            failure_category=(
                FailureCategory(value["failure_category"])
                if value.get("failure_category")
                else None
            ),
            error_summary=value.get("error_summary"),
            reconnect_count=int(value["reconnect_count"]),
            processing_fps=float(value["processing_fps"]),
            frame_buffer_size=int(value["frame_buffer_size"]),
            frame_buffer_capacity=int(value["frame_buffer_capacity"]),
            event_queue_size=int(value["event_queue_size"]),
            event_queue_capacity=int(value["event_queue_capacity"]),
            callback_backlog=int(value["callback_backlog"]),
            dropped_event_count=int(value["dropped_event_count"]),
            frames_captured=int(value["frames_captured"]),
            frames_processed=int(value["frames_processed"]),
            capture_failures=int(value["capture_failures"]),
            events_emitted=int(value["events_emitted"]),
            event_sink_failures=int(value["event_sink_failures"]),
            worker_failures=int(value["worker_failures"]),
            transition_count=int(value["transition_count"]),
        )


@dataclass(frozen=True, slots=True)
class LifecycleOperationResult:
    camera_id: str
    operation: LifecycleOperation
    outcome: LifecycleOutcome
    generation: int | None
    status: CameraStatus | None
    error: OrchestrationFailure | None = None

    @property
    def state(self) -> WorkerState:
        return self.status.state if self.status else WorkerState.STOPPED

    @property
    def metrics(self) -> CameraMetrics:
        return self.status.metrics if self.status else CameraMetrics()

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "operation": self.operation.value,
            "outcome": self.outcome.value,
            "generation": self.generation,
            "state": self.state.value,
            "status": self.status.to_dict() if self.status else None,
            "error": self.error.to_dict() if self.error else None,
        }


FrozenScalar: TypeAlias = None | bool | int | float | str
FrozenValue: TypeAlias = "FrozenScalar | tuple[FrozenValue, ...] | FrozenPayload"


def _freeze_value(value: Any) -> FrozenValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        if "://" in value:
            raise OrchestrationError(
                FailureCategory.CALLBACK,
                None,
                "event payload must not contain connection URLs",
            )
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise OrchestrationError(
                FailureCategory.CALLBACK,
                None,
                "event payload contains a non-finite number",
            )
        return value
    if isinstance(value, Mapping):
        return FrozenPayload.from_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_value(item) for item in value)
    raise OrchestrationError(
        FailureCategory.CALLBACK,
        None,
        f"event payload contains unsupported type {type(value).__name__}",
    )


def _thaw_value(value: FrozenValue) -> Any:
    if isinstance(value, FrozenPayload):
        return value.to_dict()
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class FrozenPayload(Mapping[str, FrozenValue]):
    _items: tuple[tuple[str, FrozenValue], ...]

    @classmethod
    def from_mapping(cls, value: Mapping[Any, Any]) -> FrozenPayload:
        items: list[tuple[str, FrozenValue]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise OrchestrationError(
                    FailureCategory.CALLBACK, None, "event payload keys must be strings"
                )
            if key.casefold() in _SENSITIVE_EVENT_KEYS:
                raise OrchestrationError(
                    FailureCategory.CALLBACK,
                    None,
                    "event payload contains a prohibited sensitive field",
                )
            items.append((key, _freeze_value(item)))
        return cls(tuple(items))

    def __getitem__(self, key: str) -> FrozenValue:
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def to_dict(self) -> dict[str, Any]:
        return {key: _thaw_value(value) for key, value in self._items}


@dataclass(frozen=True, slots=True)
class RoutedAnalyticsEvent:
    camera_id: str
    generation: int
    event_type: str
    timestamp: datetime
    track_id: int | None
    zone_id: str | None
    payload: FrozenPayload
    accepted_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def from_callback(
        cls,
        camera_id: str,
        generation: int,
        event: Any,
        *,
        accepted_at: datetime | None = None,
    ) -> RoutedAnalyticsEvent:
        event_type = getattr(event, "event_type", None)
        if not isinstance(event_type, str) or not event_type.strip():
            raise OrchestrationError(
                FailureCategory.CALLBACK,
                camera_id,
                "event type must be a non-empty string",
            )
        timestamp = _aware_utc(getattr(event, "timestamp", None), "event timestamp")
        raw_payload = getattr(event, "payload", None)
        if not isinstance(raw_payload, Mapping):
            raise OrchestrationError(
                FailureCategory.CALLBACK, camera_id, "event payload must be a mapping"
            )
        return cls(
            camera_id=camera_id,
            generation=generation,
            event_type=event_type,
            timestamp=timestamp,
            track_id=getattr(event, "track_id", None),
            zone_id=getattr(event, "zone_id", None),
            payload=FrozenPayload.from_mapping(raw_payload),
            accepted_at=_aware_utc(accepted_at or datetime.now(UTC), "accepted_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "generation": self.generation,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "track_id": self.track_id,
            "zone_id": self.zone_id,
            "payload": self.payload.to_dict(),
            "accepted_at": self.accepted_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class OrchestrationHealth:
    status: CameraHealth
    active_cameras: int
    failed_cameras: int
    cameras: tuple[CameraStatus, ...]

    @classmethod
    def from_statuses(cls, statuses: Mapping[str, CameraStatus]) -> OrchestrationHealth:
        snapshot = tuple(statuses[key] for key in sorted(statuses))
        active = sum(item.state in ACTIVE_STATES for item in snapshot)
        failed = sum(item.state is WorkerState.FAILED for item in snapshot)
        degraded = failed or any(
            item.event_sink_failures or item.worker_failures for item in snapshot
        )
        health = (
            CameraHealth.FAILED
            if failed
            else CameraHealth.DEGRADED
            if degraded
            else CameraHealth.OK
        )
        return cls(health, active, failed, snapshot)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "active_cameras": self.active_cameras,
            "failed_cameras": self.failed_cameras,
            "cameras": [camera.to_dict() for camera in self.cameras],
        }
