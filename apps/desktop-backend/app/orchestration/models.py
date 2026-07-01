"""Backend-neutral orchestration state and configuration contracts."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from cv_engine import AnalyticsEvent, CVConfig


class WorkerState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    RECONNECTING = "reconnecting"
    STOPPING = "stopping"
    FAILED = "failed"


ACTIVE_STATES = frozenset(
    {
        WorkerState.STARTING,
        WorkerState.RUNNING,
        WorkerState.RECONNECTING,
        WorkerState.STOPPING,
    }
)


@dataclass(frozen=True, slots=True)
class CameraDefinition:
    """One desired camera worker configuration.

    ``source`` is intentionally excluded from repr because it commonly contains
    RTSP credentials.
    """

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
        if (
            self.reconnect_delay_seconds < 0
            or self.frame_wait_seconds <= 0
            or self.event_sink_timeout_seconds <= 0
        ):
            raise ValueError(
                "delays must be non-negative and wait/timeout values must be positive"
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
    last_frame_at: datetime | None = None
    last_processed_at: datetime | None = None

    def increment(self, **changes: int | datetime | None) -> CameraMetrics:
        values: dict[str, Any] = {}
        for name, value in changes.items():
            current = getattr(self, name)
            values[name] = (
                current + value
                if isinstance(current, int) and isinstance(value, int)
                else value
            )
        return replace(self, **values)


@dataclass(frozen=True, slots=True)
class StateTransition:
    previous: WorkerState
    current: WorkerState
    timestamp: datetime
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CameraStatus:
    camera_id: str
    state: WorkerState
    generation: int
    metrics: CameraMetrics
    updated_at: datetime
    last_error: str | None = None
    transition_count: int = 0


@dataclass(frozen=True, slots=True)
class RoutedAnalyticsEvent:
    camera_id: str
    generation: int
    event: AnalyticsEvent
    accepted_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class OrchestrationHealth:
    status: str
    active_cameras: int
    failed_cameras: int
    cameras: Mapping[str, CameraStatus]

    @classmethod
    def from_statuses(cls, statuses: Mapping[str, CameraStatus]) -> OrchestrationHealth:
        snapshot = MappingProxyType(dict(statuses))
        active = sum(item.state in ACTIVE_STATES for item in snapshot.values())
        failed = sum(item.state is WorkerState.FAILED for item in snapshot.values())
        degraded = failed or any(
            item.metrics.event_sink_failures or item.metrics.worker_failures
            for item in snapshot.values()
        )
        return cls("degraded" if degraded else "ok", active, failed, snapshot)
