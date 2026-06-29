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
        self._closed = False
        self._next_reconnect_at = 0.0

    @property
    def is_opened(self) -> bool:
        with self._lock:
            return bool(self._cap is not None and self._cap.isOpened())

    def read(self) -> tuple[bool, np.ndarray | None]:
        with self._lock:
            if self._closed:
                return False, None
            if self._cap is None or not self._cap.isOpened():
                if time.monotonic() < self._next_reconnect_at or not self._connect():
                    return False, None
            success, frame = self._cap.read()
            if success and frame is not None:
                return True, frame
            self._release_locked()
            self._next_reconnect_at = time.monotonic() + self.reconnect_delay
            return False, None

    def _connect(self) -> bool:
        self._release_locked()
        if self._is_rtsp:
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|fflags;nobuffer")
            cap = self._capture_factory(self.source, cv2.CAP_FFMPEG)
        else:
            cap = self._capture_factory(self.source)
        self._cap = cap
        if not cap.isOpened():
            self._release_locked()
            self._next_reconnect_at = time.monotonic() + self.reconnect_delay
            return False
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if self._is_rtsp:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"H264"))
        if self.width is not None:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        if self.height is not None:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if self.fps is not None:
            cap.set(cv2.CAP_PROP_FPS, self.fps)
        return True

    @property
    def _is_rtsp(self) -> bool:
        return isinstance(self.source, str) and self.source.lower().startswith(("rtsp://", "rtsps://"))

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._release_locked()

    def _release_locked(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def __enter__(self) -> ResilientCapture:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
