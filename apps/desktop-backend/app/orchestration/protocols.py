"""Narrow integration contracts owned by adjacent desktop-backend modules."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

import numpy as np

from cv_engine import AnalyticsEvent, AnalyticsResult

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

    def remove_job(self, job_id: str) -> None: ...


class Capture(Protocol):
    def read(self) -> tuple[bool, np.ndarray | None]: ...

    def close(self) -> None: ...


class Pipeline(Protocol):
    async def process_if_due(
        self, frame: np.ndarray, timestamp: datetime
    ) -> AnalyticsResult | None: ...

    async def aclose(self, *, cancel_pending: bool = False) -> None: ...


CaptureFactory = Callable[[CameraDefinition], Capture]
PipelineFactory = Callable[
    [CameraDefinition, Callable[[AnalyticsEvent], Awaitable[None]]], Pipeline
]
