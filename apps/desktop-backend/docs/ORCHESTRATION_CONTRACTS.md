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

Agent-2 supplies three narrow adapters from `app.orchestration.protocols`:

- `CameraCatalog.list_enabled_cameras()` returns validated camera definitions
  with decrypted sources. A source is passed only to that camera's capture
  factory and is never included in public status or events.
- `EventSink.emit()` persists an orchestration-owned `RoutedAnalyticsEvent`.
  It contains primitives, UTC timestamps, and recursively frozen payload data;
  it exposes no CV-engine event object. `to_dict()` returns a fresh,
  deterministic JSON-compatible copy. Unsupported, non-finite, URL-bearing, or
  sensitive payload data is rejected as a callback failure.
- `Scheduler` provides only `add_job` and sync-or-async `remove_job`.

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

Agent-2 retains ownership of source decryption, SQLCipher, authentication,
persistence transactions, FastAPI lifespan, and HTTP/WebSocket adapters.

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
crosses this boundary. Agent-2 can map manager methods to local start, stop,
restart, status, health, and stream endpoints without inventing missing state.

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
