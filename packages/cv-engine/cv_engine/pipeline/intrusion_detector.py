"""Convert qualifying zone entries into rate-limited intrusion events."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..types import IntrusionEvent, ZoneEvent, ZoneEventType


class IntrusionDetector:
    def __init__(
        self,
        zone_ids: frozenset[str],
        cooldown_seconds: float = 5.0,
        retention_margin_seconds: float = 60.0,
    ) -> None:
        if cooldown_seconds < 0 or retention_margin_seconds < 0:
            raise ValueError("cooldown and retention margin must not be negative")
        self.zone_ids = zone_ids
        self.cooldown = timedelta(seconds=cooldown_seconds)
        self.retention = self.cooldown + timedelta(seconds=retention_margin_seconds)
        self._last_alert: dict[tuple[str, int], datetime] = {}

    def check(self, zone_events: list[ZoneEvent] | tuple[ZoneEvent, ...]) -> list[IntrusionEvent]:
        intrusions: list[IntrusionEvent] = []
        if zone_events:
            self.cleanup(max(event.timestamp for event in zone_events))
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

    def cleanup(
        self,
        timestamp: datetime,
        active_track_ids: set[int] | None = None,
    ) -> int:
        """Prune expired cooldown entries and, when supplied, inactive tracks."""
        cutoff = timestamp - self.retention
        stale = {
            key
            for key, last_alert in self._last_alert.items()
            if last_alert < cutoff or (active_track_ids is not None and key[1] not in active_track_ids)
        }
        for key in stale:
            self._last_alert.pop(key, None)
        return len(stale)

    @property
    def retained_state_count(self) -> int:
        return len(self._last_alert)
