import numpy as np

from cv_engine.models.tracker import Sort, iou_batch
from cv_engine.types import Detection


def test_iou_batch_handles_overlap_and_empty_inputs() -> None:
    result = iou_batch(np.array([[0, 0, 10, 10]]), np.array([[5, 5, 15, 15]]))
    assert np.isclose(result[0, 0], 25 / 175)
    assert iou_batch(np.empty((0, 4)), np.empty((0, 4))).shape == (0, 0)


def test_sort_keeps_identity_and_expires_lost_track() -> None:
    tracker = Sort(max_age=1, min_hits=1, iou_threshold=0.1)
    first = tracker.update(np.array([[0, 0, 20, 20, 0.9]]))
    second = tracker.update(np.array([[2, 0, 22, 20, 0.8]]))
    assert first[0, 4] == second[0, 4]
    tracker.update(np.empty((0, 5)))
    tracker.update(np.empty((0, 5)))
    assert tracker.trackers == []


def test_sort_preserves_detection_metadata() -> None:
    tracker = Sort(min_hits=1)
    objects = tracker.update_objects([Detection((0, 0, 10, 10), 0.75, 2, "car")])
    assert objects[0].class_id == 2
    assert objects[0].class_name == "car"
