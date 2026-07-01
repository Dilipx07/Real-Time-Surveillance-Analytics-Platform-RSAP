"""Bounded, thread-safe latest-frame storage for independent stream consumers."""

from __future__ import annotations

import threading
from collections import deque

import numpy as np


class FrameBuffer:
    def __init__(self, maxsize: int = 5) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be positive")
        self._frames: deque[tuple[int, np.ndarray]] = deque(maxlen=maxsize)
        self._condition = threading.Condition()
        self._sequence = 0

    def put(self, frame: np.ndarray) -> int:
        if not isinstance(frame, np.ndarray) or frame.size == 0:
            raise ValueError("frame must be a non-empty numpy array")
        with self._condition:
            self._sequence += 1
            self._frames.append((self._sequence, frame.copy()))
            self._condition.notify_all()
            return self._sequence

    def get_latest(self) -> np.ndarray | None:
        with self._condition:
            return self._frames[-1][1].copy() if self._frames else None

    def get_latest_with_sequence(self) -> tuple[int, np.ndarray] | None:
        with self._condition:
            if not self._frames:
                return None
            sequence, frame = self._frames[-1]
            return sequence, frame.copy()

    def wait_for_frame(self, timeout: float = 1.0, after_sequence: int | None = None) -> np.ndarray | None:
        result = self.wait_for_frame_with_sequence(timeout, after_sequence)
        return result[1] if result else None

    def wait_for_frame_with_sequence(
        self, timeout: float = 1.0, after_sequence: int | None = None
    ) -> tuple[int, np.ndarray] | None:
        if timeout < 0:
            raise ValueError("timeout must not be negative")
        with self._condition:
            if after_sequence is None and self._frames:
                sequence, frame = self._frames[-1]
                return sequence, frame.copy()
            target = self._sequence if after_sequence is None else after_sequence
            if not self._frames or self._sequence <= target:
                self._condition.wait_for(lambda: self._sequence > target, timeout=timeout)
            if not self._frames or self._sequence <= target:
                return None
            sequence, frame = self._frames[-1]
            return sequence, frame.copy()

    def clear(self) -> None:
        with self._condition:
            self._frames.clear()

    def __len__(self) -> int:
        with self._condition:
            return len(self._frames)
