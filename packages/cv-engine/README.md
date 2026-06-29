# RSAP CV Engine

Reusable, camera-agnostic computer-vision primitives for the RSAP desktop backend. The package contains synchronous analytics components plus an async `ThreadPoolExecutor` bridge; camera-worker orchestration and persistence deliberately remain outside this package.

## Installation

Core CPU installation (tracking, zones, counting, streaming helpers, overlays):

```powershell
python -m pip install -e .\packages\cv-engine
```

Install real inference backends only where needed:

```powershell
python -m pip install -e ".\packages\cv-engine[yolo]"
python -m pip install -e ".\packages\cv-engine[face]"
```

`cv_engine` itself does not import PyTorch, Ultralytics, CUDA, dlib, or `face_recognition`. Those libraries are loaded lazily when their adapter is first used. A machine without an accelerator resolves `device="auto"` to CPU. Model weights are never bundled; provide a local model path or allow Ultralytics to manage its own model acquisition outside source control.

## Pipeline example

```python
from datetime import UTC, datetime
from pathlib import Path

from cv_engine import CVConfig, CountingLine, Zone
from cv_engine.pipeline import FramePipeline

restricted = Zone(
    id="loading-bay",
    name="Loading bay",
    vertices=((0.10, 0.10), (0.90, 0.10), (0.90, 0.90), (0.10, 0.90)),
    alert_on_entry=True,
)
config = CVConfig(
    model_path=Path("models/yolov8n.pt"),
    device="auto",
    analytics_fps=10,
    zones=(restricted,),
    intrusion_zone_ids=frozenset({"loading-bay"}),
    counting_line=CountingLine((0.0, 0.5), (1.0, 0.5)),
)

pipeline = FramePipeline(config, event_callback=queue_event)
result = await pipeline.process_if_due(frame, datetime.now(UTC))
if result is not None:
    latest_overlay = result.overlay_data
pipeline.close()
```

Coordinates are normalized by default, so one configuration can follow a stream through resolution changes. Set `normalized=False` on a zone or counting line to use pixels. For a horizontal line directed left-to-right, crossing from above to below counts `IN`; the reverse counts `OUT`.

## Streaming primitives

`ResilientCapture` handles webcams, files, and RTSP sources with low-latency OpenCV settings and scheduled reconnect attempts. It does not sleep while holding a worker hostage: a failed read returns immediately until the reconnect deadline.

`FrameBuffer` is a bounded latest-frame ring. Producers never wait for analytics consumers; slow consumers skip stale frames and receive the newest complete copy. Sequence-aware waits let independent stream and analytics loops detect fresh frames without sharing mutable image memory. This is the intended separation:

- capture loop: native camera cadence, writes to the buffer;
- stream loop: reads latest frame and encodes at 25–30 FPS;
- analytics loop: calls `process_if_due`, capped by `analytics_fps` (10 FPS by default).

JPEG helpers default to quality 75, matching the platform bandwidth target. Public drawing helpers always return a copy and never mutate the source frame.

## Interfaces

- `YOLODetector`: typed Ultralytics wrapper, shared model instance per model/device.
- `Sort`: NumPy/SciPy Kalman tracker with Hungarian IoU matching.
- `ZoneAnalyzer`: one-shot enter/exit transitions by track ID.
- `PeopleCounter`: directional line crossings with hysteresis and duplicate suppression.
- `IntrusionDetector`: configured-zone entry alerts with per-track cooldown.
- `FaceEngine`: in-memory known-face index and person-crop recognition.
- `FramePipeline`: combines all modules and emits backend-neutral `AnalyticsEvent` values.

The detector and face engine can be injected into `FramePipeline`, enabling deterministic tests and alternative runtime backends without changing desktop-backend code.

## Validation and benchmark

From the repository root:

```powershell
pytest .\packages\cv-engine\tests -q
python -m compileall .\packages\cv-engine
python -c "import cv_engine; print('CV package import successful')"
python .\packages\cv-engine\benchmarks\benchmark_synthetic.py
```

The benchmark is intentionally model-free: it measures buffer copying, 20-object SORT updates, and 720p JPEG encoding. Actual YOLO throughput depends on weights, input size, CPU/GPU, and backend versions and should be measured on the deployment host.
