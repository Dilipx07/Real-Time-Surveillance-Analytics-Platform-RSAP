import asyncio
from datetime import UTC, datetime

import numpy as np

from cv_engine import CVConfig, CountingLine, Detection, Zone
from cv_engine.pipeline import FramePipeline


class SyntheticDetector:
    def __init__(self) -> None:
        self.y = 20

    def detect(self, frame: np.ndarray) -> list[Detection]:
        y = self.y
        self.y += 8
        return [Detection((40, y, 60, y + 20), 0.95, 0, "person")]


def test_synthetic_pipeline_tracks_crossing_and_emits_events() -> None:
    zone = Zone("restricted", "Restricted", ((0.25, 0.45), (0.75, 0.45), (0.75, 0.95), (0.25, 0.95)))
    config = CVConfig(
        zones=(zone,),
        counting_line=CountingLine((0, 0.5), (1, 0.5)),
        intrusion_zone_ids=frozenset({"restricted"}),
        tracker_min_hits=1,
        tracker_iou_threshold=0.1,
    )
    events = []
    pipeline = FramePipeline(config, events.append, detector=SyntheticDetector())
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    results = [pipeline.process(frame, datetime(2026, 1, 1, tzinfo=UTC)) for _ in range(6)]
    pipeline.close()
    assert len({result.tracked[0].track_id for result in results}) == 1
    assert results[-1].people_count.count_in == 1
    assert any(event.event_type == "intrusion" for event in events)
    assert all(result.processing_ms >= 0 for result in results)


def test_async_pipeline_runs_in_executor_and_rate_limits() -> None:
    config = CVConfig(analytics_fps=1, tracker_min_hits=1)
    pipeline = FramePipeline(config, detector=SyntheticDetector())
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    async def run() -> tuple[object, object]:
        first = await pipeline.process_if_due(frame, datetime.now(UTC))
        second = await pipeline.process_if_due(frame, datetime.now(UTC))
        return first, second

    first, second = asyncio.run(run())
    pipeline.close()
    assert first is not None and second is None
