"""Directional line-crossing counter keyed by stable track IDs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import hypot

from ..types import CountDirection, CountEvent, CountingLine, CountUpdate, TrackedObject


@dataclass(slots=True)
class _TrackState:
    stable_side: int
    last_seen_frame: int


class PeopleCounter:
    def __init__(
        self,
        line: CountingLine | None,
        hysteresis_pixels: float = 2.0,
        stale_after_frames: int = 30,
    ) -> None:
        if hysteresis_pixels < 0:
            raise ValueError("hysteresis_pixels must not be negative")
        if stale_after_frames < 0:
            raise ValueError("stale_after_frames must not be negative")
        self.line = line
        self.hysteresis_pixels = hysteresis_pixels
        self.stale_after_frames = stale_after_frames
        self.count_in = 0
        self.count_out = 0
        self._track_states: dict[int, _TrackState] = {}
        self._frame_index = 0

    def update(
        self,
        tracked: list[TrackedObject] | tuple[TrackedObject, ...],
        frame_shape: tuple[int, ...],
        timestamp: datetime | None = None,
    ) -> CountUpdate:
        self._frame_index += 1
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
                state = self._track_states.get(item.track_id)
                if state is not None:
                    state.last_seen_frame = self._frame_index
                continue
            side = 1 if side_value > 0 else -1
            state = self._track_states.get(item.track_id)
            if state is None:
                self._track_states[item.track_id] = _TrackState(side, self._frame_index)
                continue
            state.last_seen_frame = self._frame_index
            if state.stable_side == side:
                continue
            direction = CountDirection.IN if state.stable_side < side else CountDirection.OUT
            state.stable_side = side
            if direction is CountDirection.IN:
                self.count_in += 1
            else:
                self.count_out += 1
            events.append(CountEvent(item.track_id, direction, now))
        self.cleanup()
        return CountUpdate(self.count_in, self.count_out, tuple(events))

    def reset(self) -> None:
        self.count_in = self.count_out = 0
        self._track_states.clear()
        self._frame_index = 0

    def cleanup(self, active_track_ids: set[int] | None = None) -> int:
        """Discard expired state, optionally removing every inactive track immediately."""
        if active_track_ids is None:
            stale = {
                track_id
                for track_id, state in self._track_states.items()
                if self._frame_index - state.last_seen_frame > self.stale_after_frames
            }
        else:
            stale = set(self._track_states) - active_track_ids
        for track_id in stale:
            self._track_states.pop(track_id, None)
        return len(stale)

    @property
    def tracked_state_count(self) -> int:
        return len(self._track_states)

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
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        length = hypot(delta_x, delta_y)
        if length == 0:
            raise ValueError("counting line endpoints must differ")
        cross_product = delta_x * (point[1] - start[1]) - delta_y * (point[0] - start[0])
        return cross_product / length
