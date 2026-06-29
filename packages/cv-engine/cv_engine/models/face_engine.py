"""Optional face-recognition adapter and in-memory enrollment index."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from importlib import import_module
from typing import Any

import numpy as np

from ..types import Detection, FaceMatch


class FaceBackendUnavailableError(RuntimeError):
    """Raised when the optional face_recognition runtime is unavailable."""


@dataclass(frozen=True, slots=True)
class KnownFace:
    person_id: str
    name: str
    encoding: np.ndarray


class FaceEngine:
    """Recognize faces in person crops while reusing one known-encoding index."""

    def __init__(self, tolerance: float = 0.6, backend: Any | None = None) -> None:
        if not 0.0 < tolerance <= 1.0:
            raise ValueError("tolerance must be between 0 and 1")
        self.tolerance = tolerance
        self._backend = backend
        self._known: tuple[KnownFace, ...] = ()
        self._lock = threading.RLock()

    @property
    def backend(self) -> Any:
        if self._backend is None:
            try:
                self._backend = import_module("face_recognition")
            except ImportError as exc:
                raise FaceBackendUnavailableError(
                    "face_recognition is required; install rsap-cv-engine[face]"
                ) from exc
        return self._backend

    def set_known_faces(self, faces: list[KnownFace]) -> None:
        checked: list[KnownFace] = []
        for face in faces:
            encoding = np.asarray(face.encoding, dtype=np.float64)
            if encoding.ndim != 1:
                raise ValueError("face encodings must be one-dimensional")
            checked.append(KnownFace(face.person_id, face.name, encoding.copy()))
        with self._lock:
            self._known = tuple(checked)

    def enroll(self, person_id: str, name: str, image: np.ndarray) -> KnownFace:
        rgb = self._to_rgb(image)
        encodings = self.backend.face_encodings(rgb)
        if len(encodings) != 1:
            raise ValueError(f"enrollment image must contain exactly one face; found {len(encodings)}")
        face = KnownFace(person_id, name, np.asarray(encodings[0], dtype=np.float64))
        with self._lock:
            self._known = (*self._known, face)
        return face

    def recognize(self, frame: np.ndarray, detections: list[Detection]) -> list[FaceMatch]:
        height, width = frame.shape[:2]
        matches: list[FaceMatch] = []
        with self._lock:
            known = self._known
        known_encodings = [face.encoding for face in known]
        for detection in detections:
            if detection.class_id != 0:
                continue
            x1, y1, x2, y2 = self._clamp_bbox(detection.bbox, width, height)
            if x2 <= x1 or y2 <= y1:
                continue
            crop = self._to_rgb(frame[y1:y2, x1:x2])
            locations = self.backend.face_locations(crop)
            encodings = self.backend.face_encodings(crop, locations)
            for location, encoding in zip(locations, encodings, strict=True):
                top, right, bottom, left = location
                absolute_bbox = (float(x1 + left), float(y1 + top), float(x1 + right), float(y1 + bottom))
                if not known_encodings:
                    matches.append(FaceMatch(absolute_bbox, "Unknown", 0.0, None))
                    continue
                distances = np.asarray(self.backend.face_distance(known_encodings, encoding), dtype=float)
                best = int(np.argmin(distances))
                distance = float(distances[best])
                confidence = max(0.0, min(1.0, 1.0 - distance))
                face = known[best] if distance <= self.tolerance else None
                matches.append(FaceMatch(absolute_bbox, face.name if face else "Unknown", confidence, face.person_id if face else None))
        return matches

    @staticmethod
    def _to_rgb(image: np.ndarray) -> np.ndarray:
        if image.ndim == 2:
            return np.ascontiguousarray(np.repeat(image[:, :, None], 3, axis=2))
        return np.ascontiguousarray(image[:, :, :3][:, :, ::-1])

    @staticmethod
    def _clamp_bbox(bbox: tuple[float, float, float, float], width: int, height: int) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = bbox
        return max(0, int(x1)), max(0, int(y1)), min(width, int(x2)), min(height, int(y2))
