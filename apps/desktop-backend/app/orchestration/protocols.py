"""Narrow integration contracts; Agent-2 never imports CV-engine event types."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol

import numpy as np

from .models import CameraDefinition, RoutedAnalyticsEvent


class CameraCatalog(Protocol):
    async def list_enabled_cameras(self) -> Sequence[CameraDefinition]: ...


class EventSink(Protocol):
    async def emit(self, event: RoutedAnalyticsEvent) -> None: ...


class Scheduler(Protocol):
    def add_job(
        self,
        func: Callable[[], Awaitable[None]],
        trigger: str,
        **kwargs: Any,
    ) -> Any: ...

    def remove_job(self, job_id: str) -> None | Awaitable[None]: ...


class CallbackAnalyticsEvent(Protocol):
    event_type: str
    timestamp: datetime
    track_id: int | None
    zone_id: str | None
    payload: Mapping[str, Any]


class Capture(Protocol):
    def read(self) -> tuple[bool, np.ndarray | None]: ...

    def close(self) -> None: ...


class Pipeline(Protocol):
    async def process_if_due(self, frame: np.ndarray, timestamp: datetime) -> Any: ...

    async def aclose(self, *, cancel_pending: bool = False) -> None: ...


CaptureFactory = Callable[[CameraDefinition], Capture]
PipelineFactory = Callable[
    [CameraDefinition, Callable[[CallbackAnalyticsEvent], Awaitable[None]]], Pipeline
]
