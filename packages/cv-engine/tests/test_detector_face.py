from types import SimpleNamespace

import numpy as np

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
    engine = FaceEngine(backend=FakeFaceBackend())
    engine.set_known_faces([KnownFace("p1", "Ada", np.array([0.1, 0.2, 0.3]))])
    frame = np.zeros((30, 30, 3), dtype=np.uint8)
    matches = engine.recognize(frame, [Detection((5, 5, 25, 25), 0.9, 0, "person")])
    assert matches[0].person_id == "p1"
    assert matches[0].name == "Ada"
    assert matches[0].bbox == (5.0, 6.0, 10.0, 11.0)
