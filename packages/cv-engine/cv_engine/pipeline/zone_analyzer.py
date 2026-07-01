"""Stateful polygon entry and exit analysis."""

from __future__ import annotations

from datetime import UTC, datetime

import cv2
import numpy as np

from ..types import TrackedObject, Zone, ZoneEvent, ZoneEventType


class ZoneAnalyzer:
    def __init__(self, zones: tuple[Zone, ...], stale_after_frames: int = 30) -> None:
        self.zones = zones
        self.stale_after_frames = stale_after_frames
        self._presence: dict[tuple[str, int], bool] = {}
        self._last_seen: dict[int, int] = {}
        self._frame_index = 0

    def analyze(
        self,
        tracked: list[TrackedObject] | tuple[TrackedObject, ...],
        frame_shape: tuple[int, ...],
        timestamp: datetime | None = None,
    ) -> list[ZoneEvent]:
        self._frame_index += 1
        now = timestamp or datetime.now(UTC)
        events: list[ZoneEvent] = []
        polygons = {zone.id: self.pixel_vertices(zone, frame_shape) for zone in self.zones}
        for item in tracked:
            self._last_seen[item.track_id] = self._frame_index
            point = item.centroid
            for zone in self.zones:
                inside = cv2.pointPolygonTest(polygons[zone.id], point, False) >= 0
                key = (zone.id, item.track_id)
                was_inside = self._presence.get(key, False)
                if inside != was_inside:
                    events.append(
                        ZoneEvent(
                            zone.id,
                            zone.name,
                            item.track_id,
                            ZoneEventType.ENTER if inside else ZoneEventType.EXIT,
                            now,
                            item.confidence,
                        )
                    )
                self._presence[key] = inside
        self._discard_stale()
        return events

    @staticmethod
    def pixel_vertices(zone: Zone, frame_shape: tuple[int, ...]) -> np.ndarray:
        height, width = frame_shape[:2]
        if zone.normalized:
            points = [(x * width, y * height) for x, y in zone.vertices]
        else:
            points = list(zone.vertices)
        return np.asarray(points, dtype=np.float32)

    def _discard_stale(self) -> None:
        stale_ids = {
            track_id for track_id, frame in self._last_seen.items()
            if self._frame_index - frame > self.stale_after_frames
        }
        for track_id in stale_ids:
            self._last_seen.pop(track_id, None)
        if stale_ids:
            self._presence = {key: value for key, value in self._presence.items() if key[1] not in stale_ids}
