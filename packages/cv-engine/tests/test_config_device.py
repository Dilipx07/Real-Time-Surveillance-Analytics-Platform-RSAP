from pathlib import Path

import pytest
import pytest_asyncio

from cv_engine import CVConfig, CountingLine, Zone
from cv_engine.device import select_device


def test_config_normalizes_path_and_validates_intrusion_zones() -> None:
    zone = Zone("door", "Door", ((0.1, 0.1), (0.9, 0.1), (0.5, 0.9)))
    config = CVConfig(model_path="model.onnx", zones=(zone,), intrusion_zone_ids=frozenset({"door"}))
    assert config.model_path == Path("model.onnx")

    with pytest.raises(ValueError, match="not configured"):
        CVConfig(zones=(zone,), intrusion_zone_ids=frozenset({"missing"}))


def test_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        CVConfig(analytics_fps=0)
    with pytest.raises(ValueError):
        CountingLine((0.5, 0.5), (0.5, 0.5))


def test_explicit_cpu_never_needs_accelerator_import() -> None:
    selected = select_device("cpu")
    assert selected.resolved == "cpu"
    assert not selected.accelerated


@pytest.mark.parametrize("device", ["", "banana", "cuda:banana", "cuda:-1", "cuda:01"])
def test_invalid_device_syntax_is_rejected_before_backend_detection(device: str) -> None:
    with pytest.raises(ValueError, match="device must be"):
        select_device(device)
    with pytest.raises(ValueError, match="device must be"):
        CVConfig(device=device)


def test_valid_cuda_request_falls_back_cleanly_without_torch() -> None:
    selected = select_device("cuda:0")
    assert selected.resolved in {"cpu", "cuda:0"}


def test_test_extra_installs_async_test_support() -> None:
    assert pytest_asyncio.__version__


@pytest.mark.parametrize(
    "vertices",
    [
        ((0.0, 0.0), (1.0, 0.0), (float("nan"), 1.0)),
        ((0.0, 0.0), (1.1, 0.0), (0.0, 1.0)),
        ((0.0, 0.0), (1.0, 0.0), (1.0, 0.0)),
        ((0.0, 0.0), (0.5, 0.5), (1.0, 1.0)),
        ((0.0, 0.0), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0)),
    ],
)
def test_invalid_polygons_are_rejected(vertices: tuple[tuple[float, float], ...]) -> None:
    with pytest.raises(ValueError):
        Zone("invalid", "Invalid", vertices)


def test_zone_ids_must_be_unique() -> None:
    vertices = ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
    with pytest.raises(ValueError, match="unique"):
        CVConfig(zones=(Zone("same", "One", vertices), Zone("same", "Two", vertices)))
