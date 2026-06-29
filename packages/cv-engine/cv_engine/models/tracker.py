"""SORT tracking using a constant-velocity Kalman filter and Hungarian matching."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..types import Detection, TrackedObject


def iou_batch(boxes_a: np.ndarray, boxes_b: np.ndarray) -> np.ndarray:
    if len(boxes_a) == 0 or len(boxes_b) == 0:
        return np.empty((len(boxes_a), len(boxes_b)), dtype=float)
    a = boxes_a[:, None, :4]
    b = boxes_b[None, :, :4]
    xx1 = np.maximum(a[..., 0], b[..., 0])
    yy1 = np.maximum(a[..., 1], b[..., 1])
    xx2 = np.minimum(a[..., 2], b[..., 2])
    yy2 = np.minimum(a[..., 3], b[..., 3])
    intersection = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
    area_a = np.maximum(0.0, a[..., 2] - a[..., 0]) * np.maximum(0.0, a[..., 3] - a[..., 1])
    area_b = np.maximum(0.0, b[..., 2] - b[..., 0]) * np.maximum(0.0, b[..., 3] - b[..., 1])
    union = area_a + area_b - intersection
    return np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)


def _bbox_to_measurement(bbox: np.ndarray) -> np.ndarray:
    width = max(float(bbox[2] - bbox[0]), 1e-6)
    height = max(float(bbox[3] - bbox[1]), 1e-6)
    return np.array([bbox[0] + width / 2, bbox[1] + height / 2, width * height, width / height], dtype=float)


def _state_to_bbox(state: np.ndarray) -> np.ndarray:
    x, y, scale, ratio = state[:4]
    scale = max(float(scale), 1e-6)
    ratio = max(float(ratio), 1e-6)
    width = np.sqrt(scale * ratio)
    height = scale / width
    return np.array([x - width / 2, y - height / 2, x + width / 2, y + height / 2], dtype=float)


@dataclass(slots=True)
class _TrackMetadata:
    class_id: int = 0
    class_name: str = "person"
    confidence: float = 1.0


class KalmanBoxTracker:
    _next_id = 1

    def __init__(self, bbox: np.ndarray, metadata: _TrackMetadata | None = None) -> None:
        self.x = np.zeros(7, dtype=float)
        self.x[:4] = _bbox_to_measurement(bbox)
        self.F = np.eye(7, dtype=float)
        self.F[0, 4] = self.F[1, 5] = self.F[2, 6] = 1.0
        self.H = np.zeros((4, 7), dtype=float)
        self.H[:4, :4] = np.eye(4)
        self.P = np.eye(7, dtype=float)
        self.P[4:, 4:] *= 1000.0
        self.P *= 10.0
        self.Q = np.eye(7, dtype=float)
        self.Q[4:, 4:] *= 0.01
        self.R = np.eye(4, dtype=float)
        self.R[2:, 2:] *= 10.0
        self.id = KalmanBoxTracker._next_id
        KalmanBoxTracker._next_id += 1
        self.time_since_update = 0
        self.hits = 1
        self.hit_streak = 1
        self.age = 0
        self.metadata = metadata or _TrackMetadata()

    def predict(self) -> np.ndarray:
        if self.x[2] + self.x[6] <= 0:
            self.x[6] = 0.0
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        self.age += 1
        if self.time_since_update > 0:
            self.hit_streak = 0
        self.time_since_update += 1
        return _state_to_bbox(self.x)

    def update(self, bbox: np.ndarray, metadata: _TrackMetadata | None = None) -> None:
        measurement = _bbox_to_measurement(bbox)
        innovation = measurement - self.H @ self.x
        covariance = self.H @ self.P @ self.H.T + self.R
        gain = np.linalg.solve(covariance.T, (self.P @ self.H.T).T).T
        self.x += gain @ innovation
        self.P = (np.eye(7) - gain @ self.H) @ self.P
        self.time_since_update = 0
        self.hits += 1
        self.hit_streak += 1
        if metadata is not None:
            self.metadata = metadata

    def get_state(self) -> np.ndarray:
        return _state_to_bbox(self.x)


def _associate(detections: np.ndarray, trackers: np.ndarray, threshold: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(trackers) == 0:
        return np.empty((0, 2), dtype=int), np.arange(len(detections)), np.empty(0, dtype=int)
    ious = iou_batch(detections, trackers)
    rows, cols = linear_sum_assignment(-ious)
    matched: list[tuple[int, int]] = []
    unmatched_detections = set(range(len(detections)))
    unmatched_trackers = set(range(len(trackers)))
    for row, col in zip(rows, cols, strict=True):
        if ious[row, col] < threshold:
            continue
        matched.append((int(row), int(col)))
        unmatched_detections.discard(int(row))
        unmatched_trackers.discard(int(col))
    return (
        np.asarray(matched, dtype=int).reshape(-1, 2),
        np.asarray(sorted(unmatched_detections), dtype=int),
        np.asarray(sorted(unmatched_trackers), dtype=int),
    )


class Sort:
    def __init__(self, max_age: int = 3, min_hits: int = 2, iou_threshold: float = 0.3) -> None:
        self.max_age = max_age
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        self.trackers: list[KalmanBoxTracker] = []
        self.frame_count = 0

    def update(self, detections: np.ndarray) -> np.ndarray:
        array = np.asarray(detections, dtype=float)
        if array.size == 0:
            array = np.empty((0, 5), dtype=float)
        if array.ndim != 2 or array.shape[1] < 4:
            raise ValueError("detections must have shape (N, >=4)")
        tracked = self._update(array[:, :4], [_TrackMetadata(confidence=float(row[4]) if len(row) > 4 else 1.0) for row in array])
        return np.asarray([[*item.bbox, float(item.track_id)] for item in tracked], dtype=float).reshape(-1, 5)

    def update_objects(self, detections: list[Detection]) -> list[TrackedObject]:
        boxes = np.asarray([detection.bbox for detection in detections], dtype=float).reshape(-1, 4)
        metadata = [_TrackMetadata(d.class_id, d.class_name, d.confidence) for d in detections]
        return self._update(boxes, metadata)

    def _update(self, boxes: np.ndarray, metadata: list[_TrackMetadata]) -> list[TrackedObject]:
        self.frame_count += 1
        predicted: list[np.ndarray] = []
        valid_trackers: list[KalmanBoxTracker] = []
        for tracker in self.trackers:
            prediction = tracker.predict()
            if np.all(np.isfinite(prediction)):
                predicted.append(prediction)
                valid_trackers.append(tracker)
        self.trackers = valid_trackers
        tracker_boxes = np.asarray(predicted, dtype=float).reshape(-1, 4)
        matches, unmatched_detections, _ = _associate(boxes, tracker_boxes, self.iou_threshold)
        for detection_index, tracker_index in matches:
            self.trackers[int(tracker_index)].update(boxes[int(detection_index)], metadata[int(detection_index)])
        for detection_index in unmatched_detections:
            index = int(detection_index)
            self.trackers.append(KalmanBoxTracker(boxes[index], metadata[index]))

        result: list[TrackedObject] = []
        survivors: list[KalmanBoxTracker] = []
        for tracker in self.trackers:
            if tracker.time_since_update <= self.max_age:
                survivors.append(tracker)
            if tracker.time_since_update == 0 and (tracker.hits >= self.min_hits or self.frame_count <= self.min_hits):
                bbox = tuple(float(value) for value in tracker.get_state())
                meta = tracker.metadata
                result.append(TrackedObject(bbox, tracker.id, meta.class_id, meta.class_name, meta.confidence))
        self.trackers = survivors
        return result
