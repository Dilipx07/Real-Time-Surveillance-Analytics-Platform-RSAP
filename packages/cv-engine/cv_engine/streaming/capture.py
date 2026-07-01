"""OpenCV capture with synchronized reads and bounded reconnect behavior."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np


class ResilientCapture:
    def __init__(
        self,
        source: str | int | Path,
        reconnect_delay: float = 2.0,
        *,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
        capture_factory: Any = cv2.VideoCapture,
    ) -> None:
        if reconnect_delay < 0:
            raise ValueError("reconnect_delay must not be negative")
        self.source = str(source) if isinstance(source, Path) else source
        self.reconnect_delay = reconnect_delay
        self.width = width
        self.height = height
        self.fps = fps
        self._capture_factory = capture_factory
        self._cap: cv2.VideoCapture | Any | None = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._closed = False
        self._next_reconnect_at = 0.0
        self._connect_generation = 0

    @property
    def is_opened(self) -> bool:
        with self._lock:
            return bool(self._cap is not None and self._cap.isOpened())

    def read(self) -> tuple[bool, np.ndarray | None]:
        while True:
            with self._lock:
                if self._closed:
                    return False, None
                wait_seconds = max(0.0, self._next_reconnect_at - time.monotonic())
            if wait_seconds > 0:
                if self._stop_event.wait(wait_seconds):
                    return False, None
                continue
            with self._lock:
                if self._closed:
                    return False, None
                needs_connect = self._cap is None or not self._cap.isOpened()
                if needs_connect:
                    stale_capture = self._cap
                    self._cap = None
                    self._connect_generation += 1
                    generation = self._connect_generation
                else:
                    stale_capture = None
                    generation = None
            self._release_capture(stale_capture)
            if generation is not None and not self._connect(generation):
                return False, None
            with self._lock:
                if self._closed or self._cap is None:
                    return False, None
                success, frame = self._cap.read()
                if success and frame is not None:
                    return True, frame
                failed_capture = self._cap
                self._cap = None
                self._next_reconnect_at = time.monotonic() + self.reconnect_delay
            self._release_capture(failed_capture)
            return False, None

    def _connect(self, generation: int) -> bool:
        """Construct a candidate outside the state lock and install it atomically."""
        if self._is_rtsp:
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer")
            candidate = self._capture_factory(self.source, cv2.CAP_FFMPEG)
        else:
            candidate = self._capture_factory(self.source)
        opened = bool(candidate.isOpened())
        if opened:
            candidate.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if self._is_rtsp:
                candidate.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"H264"))
            if self.width is not None:
                candidate.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            if self.height is not None:
                candidate.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            if self.fps is not None:
                candidate.set(cv2.CAP_PROP_FPS, self.fps)

        replaced_capture = None
        with self._lock:
            install = opened and not self._closed and generation == self._connect_generation
            if install:
                replaced_capture = self._cap
                self._cap = candidate
            elif generation == self._connect_generation and not self._closed:
                self._next_reconnect_at = time.monotonic() + self.reconnect_delay
        self._release_capture(replaced_capture)
        if not install:
            self._release_capture(candidate)
        return install

    @property
    def _is_rtsp(self) -> bool:
        return isinstance(self.source, str) and self.source.lower().startswith(("rtsp://", "rtsps://"))

    def close(self) -> None:
        self._stop_event.set()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connect_generation += 1
            capture = self._cap
            self._cap = None
        self._release_capture(capture)

    @staticmethod
    def _release_capture(capture: Any | None) -> None:
        if capture is not None:
            capture.release()

    def __enter__(self) -> ResilientCapture:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
