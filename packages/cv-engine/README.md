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

For development and validation, install the test extra into a clean Python 3.12 environment. It includes pytest and pytest-asyncio without making either a runtime dependency:

```powershell
python -m pip install -e ".\packages\cv-engine[test]"
```

`cv_engine` itself does not import PyTorch, Ultralytics, CUDA, dlib, or `face_recognition`. Those libraries are loaded lazily when their adapter is first used. A machine without an accelerator resolves `device="auto"` to CPU. Model weights are never bundled; provide a local model path or allow Ultralytics to manage its own model acquisition outside source control.

Device strings are validated before hardware discovery. Accepted values are `auto`, `cpu`, `cuda`, or `cuda:` followed by either `0` or a positive decimal index without leading zeroes; examples include `cuda:0`, `cuda:1`, and `cuda:12`. Values such as `gpu`, `cuda:`, `cuda:-1`, `cuda:01`, `cuda:abc`, and `cuda:0:1` are rejected. Valid syntax does not confirm CUDA hardware or driver availability: a valid CUDA request falls back to CPU when PyTorch/CUDA is unavailable, while an out-of-range index is rejected when CUDA is available.

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
await pipeline.wait_for_callbacks()
await pipeline.aclose()
```

Callbacks may be synchronous or asynchronous. Async callbacks are dispatched to the event loop that calls `process_async`/`process_if_due`; `wait_for_callbacks()` makes completion and failures observable. Calling synchronous `process()` with an async callback requires an explicit running `event_loop` when constructing the pipeline. Pending async callbacks are bounded (100 by default) and backlog overflow is surfaced instead of growing an unbounded queue.

Use `await pipeline.aclose()` for deterministic asynchronous shutdown. It rejects new processing, waits for already accepted processing, then drains all callback tasks. `await pipeline.aclose(cancel_pending=True)` instead cancels and awaits them. Concurrent `aclose()` calls share one shutdown operation; no callback task remains active after it returns. Callback failures are logged and raised as `CallbackExecutionError`. An event callback must not directly await `aclose()` or `wait_for_callbacks()`, because either operation would wait for the callback that is awaiting it; the API rejects those cycles, so schedule shutdown in a separate task and return from the callback instead. The synchronous `close()` is idempotent for pipelines with no pending async callbacks and raises with guidance to use `aclose()` rather than silently abandoning callback work.

Coordinates are normalized by default, so one configuration can follow a stream through resolution changes. Set `normalized=False` on a zone or counting line to use pixels. For a horizontal line directed left-to-right, crossing from above to below counts `IN`; the reverse counts `OUT`.

## Streaming primitives

`ResilientCapture` handles webcams, files, and RTSP sources with low-latency OpenCV settings and scheduled reconnect attempts. Calls made during reconnect backoff wait on an interruptible event without holding the capture lock, so a disconnected camera cannot create a tight CPU loop. Capture candidates are constructed outside the state lock and installed only when their connection generation is still current. `close()` marks the instance closed promptly; candidates returning later are released and can never reactivate it.

Python cannot forcibly interrupt an arbitrary native `VideoCapture` constructor already blocked inside its backend. Closing does not wait for that constructor or its state lock; its helper thread can exit only after the native call returns, at which point the stale candidate is discarded. Retry waits themselves are interruptible.

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

Per-camera state is serialized by each `FramePipeline`; `executor_workers` must remain `1`. Separate camera pipeline instances still execute concurrently. YOLO models remain load-once per model/device key. Inference on the same shared model is serialized with a dedicated per-model lock because Ultralytics predictor state is not treated as thread-safe; unrelated model/device keys do not share that lock. This favors correctness and predictable memory use over simultaneous calls into one model instance.

This package guarantees only thread-safe local behavior for each `FramePipeline`, `FrameBuffer`, and `ResilientCapture` instance. Application-level camera-worker ownership, duplicate-start prevention, and worker start/stop orchestration belong to Agent-3's desktop backend; this package does not implement distributed or process-wide camera ownership.

People-count hysteresis is measured as perpendicular pixel distance. Track state expires after a configurable number of frames, while valid `IN → OUT → IN` cycles remain countable. Intrusion cooldown entries are removed by retention time and can also be cleaned against active track IDs.

`FaceMatch.confidence` is a clamped similarity heuristic (`1 - face distance`), not a calibrated probability. Face encodings default to the 128-value dimension expected by `face_recognition`; alternative test/backends must configure their dimension explicitly.

Public detection and tracked-object boxes require exactly four finite, non-negative coordinates with positive width and height. Detector output is clipped to frame bounds before those validated types are created. `Sort` requires `max_age >= 0`, `min_hits >= 1`, and a finite `iou_threshold` in `[0, 1]`; booleans are not accepted as numeric configuration values.

## Validation and benchmark

From the repository root:

```powershell
python -m pip install -e ".\packages\cv-engine[test]"
python -m pytest .\packages\cv-engine\tests -q
python -m compileall .\packages\cv-engine
python -c "import cv_engine; print('CV package import successful')"
python .\packages\cv-engine\benchmarks\benchmark_synthetic.py
```

The benchmark is intentionally model-free: it measures buffer copying, 20-object SORT updates, and 720p JPEG encoding. Actual YOLO throughput depends on weights, input size, CPU/GPU, and backend versions and should be measured on the deployment host.
