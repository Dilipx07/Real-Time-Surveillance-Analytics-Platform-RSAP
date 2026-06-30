from types import SimpleNamespace

import numpy as np
import threading
import time
import pytest

from cv_engine.models.detector import YOLODetector
from cv_engine.models.face_engine import FaceEngine, KnownFace
from cv_engine.types import Detection


class TensorLike:
    def __init__(self, value: np.ndarray) -> None:
        self.value = value

    def detach(self) -> "TensorLike":
        return self

    def cpu(self) -> "TensorLike":
        return self

    def numpy(self) -> np.ndarray:
        return self.value


class FakeModel:
    def predict(self, **kwargs: object) -> list[SimpleNamespace]:
        assert kwargs["device"] == "cpu"
        boxes = SimpleNamespace(
            xyxy=TensorLike(np.array([[1, 2, 30, 40]], dtype=float)),
            conf=TensorLike(np.array([0.91])),
            cls=TensorLike(np.array([0])),
        )
        return [SimpleNamespace(boxes=boxes, names={0: "person"})]


class FakeFaceBackend:
    @staticmethod
    def face_locations(image: np.ndarray) -> list[tuple[int, int, int, int]]:
        return [(1, 5, 6, 0)]

    @staticmethod
    def face_encodings(image: np.ndarray, locations: object = None) -> list[np.ndarray]:
        return [np.array([0.1, 0.2, 0.3])]

    @staticmethod
    def face_distance(known: list[np.ndarray], encoding: np.ndarray) -> np.ndarray:
        return np.linalg.norm(np.asarray(known) - encoding, axis=1)


def test_detector_maps_backend_results_to_typed_detections() -> None:
    detector = YOLODetector("fake.pt", device="cpu", model_factory=lambda _: FakeModel())
    detections = detector.detect(np.zeros((50, 50, 3), dtype=np.uint8))
    assert detections == [Detection((1.0, 2.0, 30.0, 40.0), 0.91, 0, "person")]
    assert detector.model is detector.model


def test_face_engine_matches_known_face_in_person_crop() -> None:
    engine = FaceEngine(backend=FakeFaceBackend(), encoding_dimension=3)
    engine.set_known_faces([KnownFace("p1", "Ada", np.array([0.1, 0.2, 0.3]))])
    frame = np.zeros((30, 30, 3), dtype=np.uint8)
    matches = engine.recognize(frame, [Detection((5, 5, 25, 25), 0.9, 0, "person")])
    assert matches[0].person_id == "p1"
    assert matches[0].name == "Ada"
    assert matches[0].bbox == (5.0, 6.0, 10.0, 11.0)


def test_shared_model_predict_is_serialized_per_cache_key() -> None:
    class ConcurrentModel:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def predict(self, **_: object) -> list[object]:
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.03)
            with self.lock:
                self.active -= 1
            return []

    model = ConcurrentModel()
    first = YOLODetector("shared-lock-regression.pt", device="cpu", model_factory=lambda _: model)
    second = YOLODetector("shared-lock-regression.pt", device="cpu", model_factory=lambda _: model)
    frame = np.zeros((20, 20, 3), dtype=np.uint8)
    threads = [threading.Thread(target=detector.detect, args=(frame,)) for detector in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert first.model is second.model
    assert model.max_active == 1


def test_detector_clips_boxes_and_skips_malformed_results() -> None:
    class ClippingModel(FakeModel):
        def predict(self, **kwargs: object) -> list[SimpleNamespace]:
            boxes = SimpleNamespace(
                xyxy=TensorLike(np.array([[-5, -8, 80, 90], [1, 1, np.nan, 5]], dtype=float)),
                conf=TensorLike(np.array([0.9, 0.8])),
                cls=TensorLike(np.array([0, 0])),
            )
            return [SimpleNamespace(boxes=boxes, names={0: "person"})]

    detector = YOLODetector("clip-regression.pt", device="cpu", model_factory=lambda _: ClippingModel())
    detections = detector.detect(np.zeros((50, 60, 3), dtype=np.uint8))
    assert [item.bbox for item in detections] == [(0.0, 0.0, 60.0, 50.0)]


@pytest.mark.parametrize(
    "frame",
    [
        np.array([], dtype=np.uint8),
        np.zeros((2, 2, 2), dtype=np.uint8),
        np.zeros((1, 2, 2, 3), dtype=np.uint8),
    ],
)
def test_detector_rejects_invalid_frame_shapes(frame: np.ndarray) -> None:
    detector = YOLODetector("invalid-frame-regression.pt", device="cpu", model_factory=lambda _: FakeModel())
    with pytest.raises(ValueError, match="frame"):
        detector.detect(frame)


def test_face_engine_rejects_dimension_mismatch_and_handles_unknown() -> None:
    engine = FaceEngine(backend=FakeFaceBackend(), encoding_dimension=3)
    with np.testing.assert_raises(ValueError):
        engine.set_known_faces([KnownFace("bad", "Bad", np.array([0.1, 0.2]))])
    matches = engine.recognize(
        np.zeros((30, 30, 3), dtype=np.uint8),
        [Detection((5, 5, 25, 25), 0.9, 0, "person")],
    )
    assert matches[0].person_id is None
    assert matches[0].name == "Unknown"
    assert matches[0].confidence == 0.0
