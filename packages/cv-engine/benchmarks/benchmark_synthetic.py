"""Run repeatable, model-free performance checks from the package directory."""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable

import numpy as np

from cv_engine.models.tracker import Sort
from cv_engine.streaming import FrameBuffer
from cv_engine.utils import encode_jpeg


def rate(label: str, iterations: int, operation: Callable[[], object]) -> None:
    timings: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        operation()
        timings.append(time.perf_counter() - started)
    total = sum(timings)
    print(f"{label}: {iterations / total:,.1f} ops/s, p50 {statistics.median(timings) * 1000:.3f} ms")


def main() -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    buffer = FrameBuffer(maxsize=5)
    tracker = Sort(min_hits=1)
    detections = np.array([[i * 30, 10, i * 30 + 25, 80, 0.9] for i in range(20)], dtype=float)
    rate("frame buffer put/latest", 200, lambda: (buffer.put(frame), buffer.get_latest()))
    rate("SORT 20 objects", 200, lambda: tracker.update(detections))
    rate("JPEG 720p quality=75", 30, lambda: encode_jpeg(frame, 75))


if __name__ == "__main__":
    main()
