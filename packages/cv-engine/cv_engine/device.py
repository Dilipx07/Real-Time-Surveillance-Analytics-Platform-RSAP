"""Optional accelerator discovery without importing GPU frameworks at package import."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.util import find_spec
import re


_CUDA_PATTERN = re.compile(r"cuda(?::(0|[1-9][0-9]*))?\Z")


@dataclass(frozen=True, slots=True)
class DeviceSelection:
    requested: str
    resolved: str
    accelerated: bool
    reason: str


def select_device(requested: str = "auto") -> DeviceSelection:
    validate_device_syntax(requested)
    if requested == "cpu":
        return DeviceSelection(requested, "cpu", False, "CPU explicitly requested")
    if find_spec("torch") is None:
        return DeviceSelection(requested, "cpu", False, "PyTorch is not installed")

    import torch  # Lazy: never required to import cv_engine.

    if requested.startswith("cuda"):
        if torch.cuda.is_available():
            if ":" in requested:
                index = int(requested.split(":", 1)[1])
                if index >= torch.cuda.device_count():
                    raise ValueError(
                        f"CUDA device index {index} is unavailable; found {torch.cuda.device_count()} device(s)"
                    )
            return DeviceSelection(requested, requested, True, "CUDA available")
        return DeviceSelection(requested, "cpu", False, "CUDA unavailable; using CPU")
    if torch.cuda.is_available():
        return DeviceSelection(requested, "cuda:0", True, "CUDA auto-selected")
    return DeviceSelection(requested, "cpu", False, "no accelerator available")


def validate_device_syntax(requested: str) -> None:
    if requested in {"auto", "cpu"} or _CUDA_PATTERN.fullmatch(requested):
        return
    raise ValueError("device must be auto, cpu, cuda, or cuda:<non-negative integer>")
