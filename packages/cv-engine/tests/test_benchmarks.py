"""Lightweight regression guards; these intentionally avoid model inference."""

import time

import numpy as np

from cv_engine.models.tracker import Sort
from cv_engine.streaming import FrameBuffer


def test_frame_buffer_throughput_smoke() -> None:
    buffer = FrameBuffer(maxsize=5)
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    started = time.perf_counter()
    for _ in range(100):
        buffer.put(frame)
        assert buffer.get_latest() is not None
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0


def test_tracker_throughput_smoke() -> None:
    tracker = Sort(min_hits=1)
    detections = np.array([[i * 5, 10, i * 5 + 20, 50, 0.9] for i in range(20)], dtype=float)
    started = time.perf_counter()
    for _ in range(50):
        tracker.update(detections)
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0
