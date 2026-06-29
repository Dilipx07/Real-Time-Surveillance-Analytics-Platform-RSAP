"""Directional line-crossing counter keyed by stable track IDs."""

from __future__ import annotations

from datetime import UTC, datetime

from ..types import CountDirection, CountEvent, CountingLine, CountUpdate, TrackedObject


class PeopleCounter:
    def __init__(self, line: CountingLine | None, hysteresis_pixels: float = 2.0) -> None:
        self.line = line
        self.hysteresis_pixels = hysteresis_pixels
        self.count_in = 0
        self.count_out = 0
        self._last_side: dict[int, int] = {}
        self._counted_direction: set[tuple[int, CountDirection]] = set()

    def update(
        self,
        tracked: list[TrackedObject] | tuple[TrackedObject, ...],
        frame_shape: tuple[int, ...],
        timestamp: datetime | None = None,
    ) -> CountUpdate:
        if self.line is None:
            return CountUpdate(self.count_in, self.count_out)
        now = timestamp or datetime.now(UTC)
        start, end = self._pixel_line(frame_shape)
        events: list[CountEvent] = []
        for item in tracked:
            if item.class_id != 0:
                continue
            side_value = self._signed_distance(item.centroid, start, end)
            if abs(side_value) <= self.hysteresis_pixels:
                continue
            side = 1 if side_value > 0 else -1
            previous = self._last_side.get(item.track_id)
            self._last_side[item.track_id] = side
            if previous is None or previous == side:
                continue
            direction = CountDirection.IN if previous < side else CountDirection.OUT
            key = (item.track_id, direction)
            if key in self._counted_direction:
                continue
            self._counted_direction.add(key)
            if direction is CountDirection.IN:
                self.count_in += 1
            else:
                self.count_out += 1
            events.append(CountEvent(item.track_id, direction, now))
        return CountUpdate(self.count_in, self.count_out, tuple(events))

    def reset(self) -> None:
        self.count_in = self.count_out = 0
        self._last_side.clear()
        self._counted_direction.clear()

    def _pixel_line(self, frame_shape: tuple[int, ...]) -> tuple[tuple[float, float], tuple[float, float]]:
        assert self.line is not None
        if not self.line.normalized:
            return self.line.start, self.line.end
        height, width = frame_shape[:2]
        return (
            (self.line.start[0] * width, self.line.start[1] * height),
            (self.line.end[0] * width, self.line.end[1] * height),
        )

    @staticmethod
    def _signed_distance(point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]) -> float:
        return (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (point[0] - start[0])
