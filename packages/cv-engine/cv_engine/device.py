"""Optional accelerator discovery without importing GPU frameworks at package import."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec


@dataclass(frozen=True, slots=True)
class DeviceSelection:
    requested: str
    resolved: str
    accelerated: bool
    reason: str


def select_device(requested: str = "auto") -> DeviceSelection:
    if requested == "cpu":
        return DeviceSelection(requested, "cpu", False, "CPU explicitly requested")
    if find_spec("torch") is None:
        return DeviceSelection(requested, "cpu", False, "PyTorch is not installed")

    import torch  # Lazy: never required to import cv_engine.

    if requested.startswith("cuda"):
        if torch.cuda.is_available():
            return DeviceSelection(requested, requested, True, "CUDA available")
        return DeviceSelection(requested, "cpu", False, "CUDA unavailable; using CPU")
    if requested == "mps":
        available = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
        return DeviceSelection(requested, "mps" if available else "cpu", available, "MPS available" if available else "MPS unavailable; using CPU")
    if requested != "auto":
        raise ValueError(f"unsupported device: {requested}")
    if torch.cuda.is_available():
        return DeviceSelection(requested, "cuda:0", True, "CUDA auto-selected")
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return DeviceSelection(requested, "mps", True, "MPS auto-selected")
    return DeviceSelection(requested, "cpu", False, "no accelerator available")
