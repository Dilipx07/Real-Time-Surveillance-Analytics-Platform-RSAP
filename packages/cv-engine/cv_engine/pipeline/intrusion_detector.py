"""Convert qualifying zone entries into rate-limited intrusion events."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..types import IntrusionEvent, ZoneEvent, ZoneEventType


class IntrusionDetector:
    def __init__(self, zone_ids: frozenset[str], cooldown_seconds: float = 5.0) -> None:
        if cooldown_seconds < 0:
            raise ValueError("cooldown must not be negative")
        self.zone_ids = zone_ids
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self._last_alert: dict[tuple[str, int], datetime] = {}

    def check(self, zone_events: list[ZoneEvent] | tuple[ZoneEvent, ...]) -> list[IntrusionEvent]:
        intrusions: list[IntrusionEvent] = []
        for event in zone_events:
            if event.zone_id not in self.zone_ids or event.event_type is not ZoneEventType.ENTER:
                continue
            key = (event.zone_id, event.track_id)
            previous = self._last_alert.get(key)
            if previous is not None and event.timestamp - previous < self.cooldown:
                continue
            self._last_alert[key] = event.timestamp
            intrusions.append(
                IntrusionEvent(
                    event.zone_id,
                    event.zone_name,
                    event.track_id,
                    event.timestamp,
                    event.confidence,
                )
            )
        return intrusions
