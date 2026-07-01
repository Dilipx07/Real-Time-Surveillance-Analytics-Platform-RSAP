from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any

import numpy as np

from cv_engine import AnalyticsEvent, AnalyticsResult

from app.orchestration.models import CameraDefinition, RoutedAnalyticsEvent


class FakeCapture:
    def __init__(
        self,
        outcomes: list[bool] | None = None,
        *,
        read_delay: float = 0.001,
    ) -> None:
        self.outcomes = deque(outcomes or [])
        self.read_delay = read_delay
        self.read_calls = 0
        self.close_calls = 0
        self.closed = threading.Event()

    def read(self) -> tuple[bool, np.ndarray | None]:
        time.sleep(self.read_delay)
        self.read_calls += 1
        if self.closed.is_set():
            return False, None
        success = self.outcomes.popleft() if self.outcomes else True
        frame = np.ones((4, 4, 3), dtype=np.uint8) if success else None
        return success, frame

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()


class FakePipeline:
    def __init__(
        self,
        callback: Any,
        *,
        emit_each_frame: bool = False,
        process_delay: float = 0.0,
        fail_after: int | None = None,
    ) -> None:
        self.callback = callback
        self.emit_each_frame = emit_each_frame
        self.process_delay = process_delay
        self.fail_after = fail_after
        self.process_calls = 0
        self.aclose_calls = 0
        self.closed = asyncio.Event()

    async def process_if_due(
        self, frame: np.ndarray, timestamp: datetime
    ) -> AnalyticsResult | None:
        self.process_calls += 1
        if self.process_delay:
            await asyncio.sleep(self.process_delay)
        if self.fail_after is not None and self.process_calls >= self.fail_after:
            raise RuntimeError(
                "pipeline runtime failure at rtsp://user:password@camera/live"
            )
        if self.emit_each_frame:
            await self.callback(AnalyticsEvent("test", timestamp, None, None, {}))
        return None

    async def aclose(self, *, cancel_pending: bool = False) -> None:
        self.aclose_calls += 1
        self.closed.set()


class RecordingSink:
    def __init__(
        self, *, fail: bool = False, gate: asyncio.Event | None = None
    ) -> None:
        self.fail = fail
        self.gate = gate
        self.events: list[RoutedAnalyticsEvent] = []
        self.calls = 0

    async def emit(self, event: RoutedAnalyticsEvent) -> None:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError("sink failed with token=secret-value")
        self.events.append(event)


class CaptureFactory:
    def __init__(self, **capture_kwargs: Any) -> None:
        self.capture_kwargs = capture_kwargs
        self.instances: list[FakeCapture] = []

    def __call__(self, definition: CameraDefinition) -> FakeCapture:
        capture = FakeCapture(**self.capture_kwargs)
        self.instances.append(capture)
        return capture


class PipelineFactory:
    def __init__(self, **pipeline_kwargs: Any) -> None:
        self.pipeline_kwargs = pipeline_kwargs
        self.instances: list[FakePipeline] = []

    def __call__(self, definition: CameraDefinition, callback: Any) -> FakePipeline:
        pipeline = FakePipeline(callback, **self.pipeline_kwargs)
        self.instances.append(pipeline)
        return pipeline


async def eventually(predicate: Any, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not met before timeout")
        await asyncio.sleep(0.005)
