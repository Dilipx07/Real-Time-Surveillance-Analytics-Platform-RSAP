# Desktop orchestration contracts

This module owns process-local camera worker lifecycle, CV pipeline ownership,
bounded event routing, desired-state reconciliation, metrics, and health state.
It does not own SQLCipher schemas, authentication, FastAPI routes, central sync,
or desktop UI code.

## Lifecycle

The observable worker states are:

```text
STOPPED -> STARTING -> RUNNING <-> RECONNECTING
              |           |             |
              +-----------+-------------+-> STOPPING -> STOPPED
              +-----------+-------------+-------------> FAILED
```

`CameraWorkerManager` serializes lifecycle mutations and stores at most one
worker for a camera ID. Concurrent starts return the same generation. Restart
awaits complete shutdown of the old generation before constructing the new one.
Stop and manager shutdown are idempotent.

A worker owns exactly one capture, frame buffer, analytics pipeline, event
queue, capture task, analytics task, event-dispatch task, and supervisor task.
Capture and analytics are independent so inference cannot stall frame capture.
The CV engine performs inference in its single-worker per-camera executor.

Shutdown follows this order:

1. stop accepting events and signal the worker loops;
2. close capture to interrupt reconnect waits;
3. await capture and analytics tasks;
4. always await `FramePipeline.aclose()` and its accepted callbacks;
5. drain events accepted before shutdown and stop the dispatcher;
6. clear the frame buffer and publish the final state.

Events offered after step 1 are counted as dropped and never reach the sink.
No worker-owned async task remains after stop returns. A native OpenCV capture
constructor cannot be forcibly interrupted by Python; the CV engine discards a
candidate that returns after closure, as documented in its README.

All resource growth is bounded: frame buffers and event queues are configured
per camera; callback backlog is bounded by the same event capacity; worker
transition history defaults to 64 entries; manager status/generation retention
defaults to 256 camera IDs. Reconnect failures have an interruptible delay.

## Agent-2 adapters

Agent-2's desktop backend supplies adapters for these narrow protocols in
`app.orchestration.protocols`:

- `CameraCatalog.list_enabled_cameras()` returns complete `CameraDefinition`
  values with already-decrypted sources and validated `CVConfig` objects.
- `EventSink.emit()` durably writes one `RoutedAnalyticsEvent` to Agent-2's
  local event/offline-sync transaction. The sink must be idempotent using the
  camera ID, worker generation, and event identity assigned by its adapter.
- `Scheduler` is the small APScheduler-compatible `add_job`/`remove_job`
  surface used for a coalesced, single-instance reconciliation job.

Agent-2 owns source decryption, SQLCipher sessions, persistence, auth, API
envelopes, APScheduler startup/shutdown, and construction during FastAPI
lifespan. Optional backend failures are surfaced through event failure metrics;
they do not block capture or cause an unbounded retry queue in orchestration.

Recommended lifespan wiring:

```python
manager = CameraWorkerManager(local_event_sink)
service = CameraOrchestrationService(manager, camera_catalog, scheduler=scheduler)
await service.start()
# FastAPI serves requests
await service.stop()
```

## Agent-4-facing adapter surface

Agent-4 calls Agent-2's localhost API rather than importing orchestration. The
API adapter can map these operations without exposing camera credentials:

| UI need | Orchestration call |
|---|---|
| Start camera | `manager.start_camera(definition)` |
| Stop camera | `manager.stop_camera(camera_id)` |
| Restart after config change | `manager.restart_camera(definition)` |
| Camera list/status/metrics | `manager.statuses()` |
| Overall health | `manager.health()` |
| JPEG WebSocket source | `manager.get_frame_buffer(camera_id)` |

Suggested local routes are `POST /cameras/{id}/start`, `/stop`, `/restart`,
`GET /cameras/status`, `GET /health`, and `WS /ws/stream/{id}`. Their auth,
response models, JPEG encoding, and WebSocket lifecycle belong to Agents 2/4.

Status exposes only camera IDs, state, generation, timestamps, counters, and a
sanitized error. `CameraDefinition.source` is excluded from repr. URL userinfo
and sensitive query parameters are redacted before errors reach status/logs.

## Validation

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r .\apps\desktop-backend\requirements-orchestration-dev.txt
.\.venv\Scripts\python -m pytest .\apps\desktop-backend\tests -q
.\.venv\Scripts\python -m compileall .\apps\desktop-backend\app
```
