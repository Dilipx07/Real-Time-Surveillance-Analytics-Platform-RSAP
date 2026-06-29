from pathlib import Path

import pytest

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
