import asyncio
import threading
import time
import warnings
from datetime import UTC, datetime

import numpy as np

from cv_engine import CVConfig, CountingLine, Detection, Zone
import pytest

from cv_engine.pipeline import CallbackExecutionError, FramePipeline


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


@pytest.mark.asyncio
async def test_sync_and_async_callbacks_execute_exactly_once() -> None:
    zone = Zone("all", "All", ((0, 0), (1, 0), (1, 1), (0, 1)))
    synchronous = []
    sync_pipeline = FramePipeline(CVConfig(zones=(zone,), tracker_min_hits=1), synchronous.append, detector=SyntheticDetector())
    await sync_pipeline.process_async(np.zeros((100, 100, 3), dtype=np.uint8), datetime.now(UTC))
    assert len(synchronous) == 1
    sync_pipeline.close()

    asynchronous = []

    async def callback(event: object) -> None:
        asynchronous.append(event)

    async_pipeline = FramePipeline(CVConfig(zones=(zone,), tracker_min_hits=1), callback, detector=SyntheticDetector())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await async_pipeline.process_async(np.zeros((100, 100, 3), dtype=np.uint8), datetime.now(UTC))
        await async_pipeline.wait_for_callbacks()
    async_pipeline.close()
    assert len(asynchronous) == 1
    assert not [warning for warning in caught if "never awaited" in str(warning.message)]


@pytest.mark.asyncio
async def test_async_callback_failure_is_observable() -> None:
    zone = Zone("all-errors", "All", ((0, 0), (1, 0), (1, 1), (0, 1)))

    async def callback(_: object) -> None:
        raise RuntimeError("callback exploded")

    pipeline = FramePipeline(CVConfig(zones=(zone,), tracker_min_hits=1), callback, detector=SyntheticDetector())
    await pipeline.process_async(np.zeros((100, 100, 3), dtype=np.uint8), datetime.now(UTC))
    with pytest.raises(CallbackExecutionError) as error:
        await pipeline.wait_for_callbacks()
    pipeline.close()
    assert isinstance(error.value.__cause__, RuntimeError)
    assert str(error.value.__cause__) == "callback exploded"


@pytest.mark.asyncio
async def test_pipeline_stateful_processing_is_serialized() -> None:
    class SlowDetector(SyntheticDetector):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def detect(self, frame: np.ndarray) -> list[Detection]:
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.02)
            detections = super().detect(frame)
            with self.lock:
                self.active -= 1
            return detections

    detector = SlowDetector()
    pipeline = FramePipeline(CVConfig(tracker_min_hits=1), detector=detector)
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    results = await asyncio.gather(*(
        pipeline.process_async(frame, datetime.now(UTC)) for _ in range(5)
    ))
    pipeline.close()
    assert detector.max_active == 1
    assert len(results) == 5
    assert [result.detections[0].bbox[1] for result in results] == [20.0, 28.0, 36.0, 44.0, 52.0]
    assert pipeline._frame_count == 5


def test_unsafe_executor_worker_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="executor_workers=1"):
        CVConfig(executor_workers=2)
