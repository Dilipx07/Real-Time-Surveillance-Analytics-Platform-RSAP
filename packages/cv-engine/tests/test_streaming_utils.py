import threading
import time

import numpy as np

from cv_engine import Detection, OverlayData, TrackedObject, Zone
from cv_engine.streaming import FrameBuffer, ResilientCapture
from cv_engine.utils import decode_image, draw_detections, encode_jpeg, render_overlay, resize_to_fit


class FakeCapture:
    instances: list["FakeCapture"] = []

    def __init__(self, source: object, backend: object = None) -> None:
        self.opened = True
        self.released = False
        self.reads = 0
        self.settings: list[tuple[int, float]] = []
        self.instances.append(self)

    def isOpened(self) -> bool:
        return self.opened

    def read(self) -> tuple[bool, np.ndarray | None]:
        self.reads += 1
        if self.reads == 1:
            return True, np.full((4, 4, 3), 9, dtype=np.uint8)
        return False, None

    def set(self, key: int, value: float) -> bool:
        self.settings.append((key, value))
        return True

    def release(self) -> None:
        self.opened = False
        self.released = True


def test_frame_buffer_copies_data_and_waits_for_new_sequence() -> None:
    buffer = FrameBuffer(maxsize=2)
    original = np.zeros((2, 2), dtype=np.uint8)
    sequence = buffer.put(original)
    original[:] = 5
    assert np.all(buffer.get_latest() == 0)

    def producer() -> None:
        time.sleep(0.02)
        buffer.put(np.ones((2, 2), dtype=np.uint8))

    thread = threading.Thread(target=producer)
    thread.start()
    received = buffer.wait_for_frame_with_sequence(0.5, after_sequence=sequence)
    thread.join()
    assert received is not None and received[0] == sequence + 1


def test_resilient_capture_reads_and_releases_failed_source() -> None:
    FakeCapture.instances.clear()
    capture = ResilientCapture(0, reconnect_delay=0, capture_factory=FakeCapture)
    success, frame = capture.read()
    assert success and frame is not None
    success, frame = capture.read()
    assert not success and frame is None
    assert FakeCapture.instances[0].released
    capture.close()


def test_capture_reconnect_is_rate_limited_without_holding_lock() -> None:
    FakeCapture.instances.clear()
    capture = ResilientCapture(0, reconnect_delay=0.1, capture_factory=FakeCapture)
    assert capture.read()[0]
    assert not capture.read()[0]
    started = time.monotonic()
    success, _ = capture.read()
    elapsed = time.monotonic() - started
    capture.close()
    assert success
    assert elapsed >= 0.08
    assert len(FakeCapture.instances) == 2


def test_capture_shutdown_interrupts_reconnect_wait_promptly() -> None:
    FakeCapture.instances.clear()
    capture = ResilientCapture(0, reconnect_delay=5.0, capture_factory=FakeCapture)
    assert capture.read()[0]
    assert not capture.read()[0]
    result: list[tuple[bool, np.ndarray | None]] = []
    waiter = threading.Thread(target=lambda: result.append(capture.read()))
    waiter.start()
    time.sleep(0.05)
    probe_started = time.monotonic()
    assert not capture.is_opened
    assert time.monotonic() - probe_started < 0.1
    close_started = time.monotonic()
    capture.close()
    waiter.join(timeout=0.5)
    assert not waiter.is_alive()
    assert time.monotonic() - close_started < 0.5
    assert result == [(False, None)]


def test_image_and_overlay_helpers_do_not_mutate_source() -> None:
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    detection = Detection((10, 10, 40, 60), 0.9, 0, "person")
    drawn = draw_detections(frame, [detection])
    assert np.all(frame == 0) and np.any(drawn != 0)
    encoded = encode_jpeg(drawn, quality=75)
    assert decode_image(encoded).shape == frame.shape
    assert resize_to_fit(frame, 50, 50).shape == (25, 50, 3)

    zone = Zone("z", "Zone", ((0, 0), (1, 0), (1, 1)))
    overlay = OverlayData((TrackedObject((10, 10, 40, 60), 1),), (zone,), (), 2, 1)
    assert np.any(render_overlay(frame, overlay) != 0)
    assert np.all(frame == 0)
