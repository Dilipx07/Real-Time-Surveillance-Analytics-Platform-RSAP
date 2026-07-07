"""Model-free synthetic benchmark for Agent-3 lifecycle orchestration."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "cv-engine"))
sys.path.insert(0, str(ROOT / "apps" / "desktop-backend"))

from app.orchestration import CameraDefinition, CameraWorkerManager  # noqa: E402
from app.orchestration.models import RoutedAnalyticsEvent  # noqa: E402

CAMERA_COUNT = 8
CYCLES_PER_CAMERA = 10
EVENT_QUEUE_CAPACITY = 16


class SyntheticCapture:
    def __init__(self) -> None:
        self.closed = threading.Event()
        self.close_calls = 0

    def read(self) -> tuple[bool, np.ndarray | None]:
        time.sleep(0.001)
        if self.closed.is_set():
            return False, None
        return True, np.ones((4, 4, 3), dtype=np.uint8)

    def close(self) -> None:
        self.close_calls += 1
        self.closed.set()


class SyntheticPipeline:
    def __init__(self, callback: Any) -> None:
        self.callback = callback
        self.close_calls = 0

    async def process_if_due(self, frame: np.ndarray, timestamp: datetime) -> object:
        await self.callback(
            SimpleNamespace(
                event_type="synthetic",
                timestamp=timestamp,
                track_id=None,
                zone_id=None,
                payload={"objects": 1},
            )
        )
        return object()

    async def aclose(self, *, cancel_pending: bool = False) -> None:
        self.close_calls += 1


class CountingSink:
    def __init__(self) -> None:
        self.events = 0

    async def emit(self, event: RoutedAnalyticsEvent) -> None:
        self.events += 1


async def benchmark() -> None:
    captures: list[SyntheticCapture] = []
    pipelines: list[SyntheticPipeline] = []
    sink = CountingSink()

    def capture_factory(definition: CameraDefinition) -> SyntheticCapture:
        capture = SyntheticCapture()
        captures.append(capture)
        return capture

    def pipeline_factory(
        definition: CameraDefinition, callback: Any
    ) -> SyntheticPipeline:
        pipeline = SyntheticPipeline(callback)
        pipelines.append(pipeline)
        return pipeline

    manager = CameraWorkerManager(
        sink,
        capture_factory=capture_factory,
        pipeline_factory=pipeline_factory,
        max_active_workers=CAMERA_COUNT,
    )
    definitions = [
        CameraDefinition(
            f"synthetic-{index}",
            index,
            event_queue_size=EVENT_QUEUE_CAPACITY,
            frame_buffer_size=4,
            frame_wait_seconds=0.005,
        )
        for index in range(CAMERA_COUNT)
    ]

    started = time.perf_counter()
    for _ in range(CYCLES_PER_CAMERA):
        await asyncio.gather(*(manager.start_camera(item) for item in definitions))
        await asyncio.sleep(0.02)
        await asyncio.gather(
            *(manager.stop_camera(item.camera_id) for item in definitions)
        )
    await manager.shutdown()
    elapsed = time.perf_counter() - started
    pending = [
        task
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task() and task.get_name().startswith("camera-")
    ]

    pipeline_close_count = sum(item.close_calls for item in pipelines)
    capture_close_count = sum(item.close_calls for item in captures)
    expected_workers = CAMERA_COUNT * CYCLES_PER_CAMERA
    if (
        pipeline_close_count != expected_workers
        or capture_close_count != expected_workers
    ):
        raise RuntimeError("synthetic benchmark detected incomplete resource cleanup")
    if manager.active_camera_ids() or pending:
        raise RuntimeError("synthetic benchmark detected leaked worker ownership")

    print("Synthetic orchestration benchmark (not inference throughput)")
    print(f"fake camera count: {CAMERA_COUNT}")
    print(f"cycles per camera: {CYCLES_PER_CAMERA}")
    print(f"events emitted: {sink.events}")
    print(f"queue capacity: {EVENT_QUEUE_CAPACITY}")
    print(f"elapsed seconds: {elapsed:.3f}")
    print(f"final active workers: {len(manager.active_camera_ids())}")
    print(f"final pending worker tasks: {len(pending)}")
    print(f"pipeline close count: {pipeline_close_count}/{expected_workers}")
    print(f"capture close count: {capture_close_count}/{expected_workers}")


if __name__ == "__main__":
    asyncio.run(benchmark())
