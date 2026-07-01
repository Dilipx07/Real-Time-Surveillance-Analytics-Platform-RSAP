"""RSAP reusable computer-vision engine."""

from .config import CVConfig
from .device import DeviceSelection, select_device
from .types import (
    AnalyticsEvent,
    AnalyticsResult,
    CountDirection,
    CountEvent,
    CountingLine,
    CountUpdate,
    Detection,
    FaceMatch,
    IntrusionEvent,
    OverlayData,
    TrackedObject,
    Zone,
    ZoneEvent,
    ZoneEventType,
)

__all__ = [
    "AnalyticsEvent", "AnalyticsResult", "CVConfig", "CountDirection",
    "CountEvent", "CountingLine", "CountUpdate", "Detection",
    "DeviceSelection", "FaceMatch", "IntrusionEvent", "OverlayData",
    "TrackedObject", "Zone", "ZoneEvent", "ZoneEventType", "select_device",
]

__version__ = "1.0.0"
