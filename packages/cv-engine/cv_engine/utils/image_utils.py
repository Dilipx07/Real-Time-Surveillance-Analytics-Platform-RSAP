"""Validated image conversion helpers used by stream adapters."""

from __future__ import annotations

import cv2
import numpy as np


def encode_jpeg(frame: np.ndarray, quality: int = 75) -> bytes:
    if not 1 <= quality <= 100:
        raise ValueError("JPEG quality must be between 1 and 100")
    success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        raise ValueError("OpenCV could not encode the frame")
    return encoded.tobytes()


def decode_image(data: bytes | bytearray | memoryview) -> np.ndarray:
    if not data:
        raise ValueError("image data is empty")
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("invalid or unsupported encoded image")
    return image


def resize_to_fit(frame: np.ndarray, max_width: int, max_height: int, *, upscale: bool = False) -> np.ndarray:
    if max_width < 1 or max_height < 1:
        raise ValueError("maximum dimensions must be positive")
    height, width = frame.shape[:2]
    scale = min(max_width / width, max_height / height)
    if not upscale:
        scale = min(scale, 1.0)
    if scale == 1.0:
        return frame.copy()
    dimensions = (max(1, round(width * scale)), max(1, round(height * scale)))
    interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    return cv2.resize(frame, dimensions, interpolation=interpolation)
