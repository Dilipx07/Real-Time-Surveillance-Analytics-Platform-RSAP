from datetime import UTC, datetime, timedelta

from cv_engine import CountingLine, TrackedObject, Zone, ZoneEventType
from cv_engine.pipeline.intrusion_detector import IntrusionDetector
from cv_engine.pipeline.people_counter import PeopleCounter
from cv_engine.pipeline.zone_analyzer import ZoneAnalyzer


NOW = datetime(2026, 1, 1, tzinfo=UTC)


def person(track_id: int, x: float, y: float) -> TrackedObject:
    return TrackedObject((x - 5, y - 5, x + 5, y + 5), track_id, 0, "person", 0.9)


def test_zone_analyzer_emits_one_enter_and_exit_transition() -> None:
    zone = Zone("z1", "Restricted", ((0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)))
    analyzer = ZoneAnalyzer((zone,))
    assert analyzer.analyze([person(1, 10, 10)], (100, 100, 3), NOW) == []
    entered = analyzer.analyze([person(1, 50, 50)], (100, 100, 3), NOW)
    assert entered[0].event_type is ZoneEventType.ENTER
    assert analyzer.analyze([person(1, 55, 55)], (100, 100, 3), NOW) == []
    exited = analyzer.analyze([person(1, 90, 90)], (100, 100, 3), NOW)
    assert exited[0].event_type is ZoneEventType.EXIT


def test_people_counter_counts_each_direction_once_per_track() -> None:
    counter = PeopleCounter(CountingLine((0.0, 0.5), (1.0, 0.5)), hysteresis_pixels=1)
    counter.update([person(7, 50, 40)], (100, 100, 3), NOW)
    update = counter.update([person(7, 50, 60)], (100, 100, 3), NOW)
    assert (update.count_in, update.count_out) == (1, 0)
    counter.update([person(7, 50, 40)], (100, 100, 3), NOW)
    repeated = counter.update([person(7, 50, 60)], (100, 100, 3), NOW)
    assert (repeated.count_in, repeated.count_out) == (1, 1)


def test_intrusion_detector_filters_zones_and_applies_cooldown() -> None:
    zone = Zone("z1", "Restricted", ((0, 0), (1, 0), (1, 1)))
    analyzer = ZoneAnalyzer((zone,))
    event = analyzer.analyze([person(3, 20, 20)], (100, 100, 3), NOW)[0]
    detector = IntrusionDetector(frozenset({"z1"}), cooldown_seconds=10)
    assert len(detector.check([event])) == 1
    assert detector.check([event]) == []
    later = type(event)(event.zone_id, event.zone_name, event.track_id, event.event_type, NOW + timedelta(seconds=11), event.confidence)
    assert len(detector.check([later])) == 1
