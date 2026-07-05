# Desktop orchestration contracts

This module owns process-local camera lifecycle, CV pipeline ownership, bounded
event routing, desired-state reconciliation, status snapshots, and health. It
does not own SQLCipher, authentication, FastAPI routes, central sync, or UI.

## Lifecycle and locking

The guarded state machine is:

```text
STOPPED -> STARTING -> RUNNING <-> RECONNECTING
              |           |             |
              +-----------+-------------+-> STOPPING -> STOPPED
              +-----------+-------------+-------------> FAILED
```

Invalid transitions raise a categorized internal orchestration error. Worker
transition history is a 64-entry deque while `transition_count` remains a
cumulative counter.

The manager uses a short-lived registry lock and weakly retained per-camera
lifecycle locks. Operations for the same camera serialize; operations for
different cameras proceed independently. Lock order is: obtain or retain the
camera lock reference under the registry lock, release the registry lock,
acquire the camera lock, then briefly reacquire the registry lock for atomic
ownership changes. No registry lock is held during capture construction,
pipeline construction, worker start/stop, callback settlement, or resource
closure.

Workers remain registered while `STOPPING` and are removed only after cleanup.
Start arriving during stop waits for the old generation, then creates exactly
one replacement. Start, stop, and restart return `LifecycleOperationResult`
with stable outcomes: `started`, `already_running`, `stopped`,
`already_stopped`, `restarted`, or `failed`.

`max_active_workers` bounds active ownership and defaults to eight, matching the
platform camera limit. Concurrent final-slot starts are decided atomically.

## Cancellation-safe shutdown

The first manager shutdown caller closes start admission and creates one named
internal shutdown task. Every caller awaits that same task through
`asyncio.shield()`, so cancelling a waiter cannot cancel cleanup. Shutdown
acquires each camera's lifecycle lock, stops it, awaits all owned tasks, records
status, and removes ownership only after settlement.

Cleanup order per worker is:

1. close event admission and signal capture/analytics loops;
2. close capture exactly once;
3. await capture and analytics tasks;
4. call pipeline `aclose()` exactly once;
5. drain accepted FIFO events and stop the dispatcher;
6. clear the frame buffer and publish `STOPPED` or `FAILED`.

Cleanup failures are sanitized, categorized, retained in status, aggregated by
the manager, and surfaced as `ShutdownError` after all workers settle. Repeated
shutdown returns the same success or failure. A native OpenCV constructor
cannot be forcibly interrupted; it runs outside the event loop, its worker
remains owned, and shutdown waits until it returns so the late candidate can be
closed rather than leaked.

## Capture and reconnect

`CameraDefinition` requires finite float settings. Reconnect delay is bounded
from 0.05 through 300 seconds. Zero, negative, NaN, and infinite values are
rejected, preventing retry busy-spins. Reconnect waits are interruptible by the
worker stop event. Capture and analytics remain independent, and frame storage
is a bounded latest-frame ring.

## Public errors

External lifecycle calls raise `OrchestrationError` with a stable
`FailureCategory`: `configuration`, `capacity`, `capture`, `model`, `pipeline`,
`callback`, `sink`, `scheduler`, `shutdown`, or `internal`. Public exception
text, `repr`, logs, status, and operation DTOs contain only sanitized summaries.
Connection URLs are replaced wholesale and sensitive assignments are removed.
Original exceptions are retained only as private diagnostic causes.

## Agent-2 event and scheduler boundary

Production adapters live in `app/orchestration/adapters.py`; orchestration core
does not import Agent-2 repositories, services, SQLCipher, or FastAPI:

- `Agent2CameraCatalog.list_enabled_cameras()` reads only active cameras through
  `CameraRepository.list_active()`, requires the persisted active session to
  hold `camera.read`, respects the licensed camera limit, and returns validated
  camera definitions
  with decrypted sources. A source is passed only to that camera's capture
  factory and is never included in public status or events.
- `Agent2EventSink.emit()` resolves the active persisted session and persists an
  orchestration-owned `RoutedAnalyticsEvent` through `AnalyticsService`, which
  enforces `analytics.write`, analytics-module licensing, the local analytics
  transaction, and durable sync enqueueing.
  It contains primitives, UTC timestamps, and recursively frozen payload data;
  it exposes no CV-engine event object. `to_dict()` returns a fresh,
  deterministic JSON-compatible copy. Unsupported, non-finite, URL-bearing, or
  sensitive payload data is rejected as a callback failure.
- `AsyncioPeriodicScheduler` implements `Scheduler` without an external
  dependency. Each job has one owned task, executes sequentially, observes and
  sanitizes callback failures, and awaits cancellation during removal and
  shutdown.

Camera analytics configuration maps the supported scalar `CVConfig` fields,
optional counting-line data, and complete polygon zones. Product feature flags
that are not `CVConfig` fields remain Agent-2 licence metadata and are not
passed to CV-engine. A malformed runtime zone/configuration is rejected as a
categorized `configuration` failure; it is never silently coerced.

Each camera has one bounded FIFO event queue. Accepted events preserve callback
order for that camera. There is deliberately no cross-camera ordering because
workers execute independently. Queue overflow drops the new event and increments
`dropped_event_count`; a slow sink is bounded by `event_sink_timeout_seconds`.
Sink failure is observable and later events continue.

`CameraOrchestrationService` owns shared start/stop tasks and registers every
active reconciliation task. Stop closes reconciliation admission, removes and
awaits scheduler shutdown, awaits in-progress reconciliation, and only then
shuts down the manager. Scheduler registration failure rolls back workers.
Overlapping ticks coalesce. Desired catalog state wins on the next scheduled
tick after a manual camera stop; service stop always has final precedence.

Agent-2 retains ownership of source decryption, SQLCipher, authentication, and
persistence transactions. The shared desktop composition root owns process
lifespan wiring and the authenticated HTTP adapter described below.

## Production container and lifespan

`app/container.py` constructs exactly one `Agent2CameraCatalog`,
`Agent2EventSink`, `AsyncioPeriodicScheduler`, `CameraWorkerManager`, and
`CameraOrchestrationService` per application container. Database migration and
verification complete before orchestration starts. If initial reconciliation
or scheduler registration fails, service startup rolls back all workers and
container startup fails without publishing the FastAPI application state.

Container close is a shared shielded task. It stops reconciliation and workers,
shuts down the scheduler, closes the central client, then closes the database;
later cleanup continues even if a close caller is cancelled. FastAPI's lifespan
calls this start/close pair, and both `/health` and the authenticated
orchestration health route expose non-blocking, secret-free health snapshots.

The app-scoped Docker context cannot copy the monorepo sibling CV package.
`requirements.txt` therefore pins `rsap-cv-engine` to the immutable merged
Agent-6 source commit; local development and tests continue to replace it with
the editable sibling package from `requirements-orchestration-dev.txt`.

## Authenticated Agent-4 routes

`app/routers/orchestration.py` exposes:

```text
GET  /orchestration/health
GET  /orchestration/cameras
GET  /orchestration/cameras/{camera_id}/status
POST /orchestration/cameras/{camera_id}/start
POST /orchestration/cameras/{camera_id}/stop
POST /orchestration/cameras/{camera_id}/restart
```

Every route requires Agent-2's bearer plus session-token dependency. Read and
status routes require existing deny-by-default `camera.read`; lifecycle changes
require `camera.update`, then revalidate camera readability and active runtime
configuration. These existing permissions preserve the central permission
contract without inventing a second role vocabulary. Responses use the desktop
envelope and existing frozen Agent-4 DTO serialization. Unknown cameras return
404, inactive cameras cannot be started/restarted, and no connection source is
included in route data or errors.

## Agent-4 status boundary

`CameraStatus`, `LifecycleOperationResult`, and `OrchestrationHealth` are frozen
snapshots with explicit `to_dict()` methods. `CameraStatus.from_dict()` supports
round-trip validation. Public data includes:

- camera ID, generation, lifecycle state, health, and running flag;
- UTC update, last-frame, last-processing, and last-event timestamps;
- failure category and redacted summary;
- reconnect count and processing FPS;
- current/capacity values for frame and event queues;
- callback backlog, dropped events, and processing/event counters;
- cumulative transition count.

No task, capture, pipeline, connection source, mutable payload, or mapping proxy
crosses this boundary. The production router maps manager methods to local
start, stop, restart, status, and health endpoints. Live frame streaming remains
deferred to the desktop streaming component/Agent-7 because no authenticated
stream transport contract exists in Agent-2; the frame buffer is not exposed by
these JSON endpoints.

## Resource bounds

| Resource | Bound |
|---|---|
| Active workers | `max_active_workers`, default 8 |
| Frame ring | `frame_buffer_size`, default 10 |
| Event queue | `event_queue_size`, default 100 |
| CV callback tasks | same configured event capacity |
| Transition history | 64 per worker |
| Retained camera statuses/generations | `retained_statuses`, default 256 |
| Per-camera locks | weakly retained while referenced |
| Scheduler reconciliation | one active/coalesced invocation |
| Shutdown task | one shared task per manager/service |

Stopped workers and completed reconciliation tasks are removed. Completed
shutdown tasks remain as the authoritative idempotent result.

## Validation and benchmark

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r .\apps\desktop-backend\requirements-orchestration-dev.txt
.\.venv\Scripts\python -m pytest .\apps\desktop-backend\tests -q
.\.venv\Scripts\python -m compileall .\apps\desktop-backend\app\orchestration
.\.venv\Scripts\python .\apps\desktop-backend\benchmarks\benchmark_orchestration.py
```

The benchmark uses eight fake cameras, fake pipelines, and bounded event queues.
It reports lifecycle/event cleanup only. It uses no model or camera and must not
be interpreted as inference throughput.
