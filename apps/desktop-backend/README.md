# RSAP Desktop Backend Foundation

This localhost FastAPI service owns encrypted edge persistence, local authorization,
and durable central synchronization. Camera capture, CV orchestration, streaming,
and scheduling remain Agent-3 responsibilities.

## Security and configuration

- Non-test databases require SQLCipher.
- Camera URLs, person phone numbers, and cached tokens also use purpose-bound
  AES-256-GCM encryption.
- SQLCipher and field-encryption keys must be distinct, URL-safe base64 values that
  decode to 32 bytes. Zero, repeated-byte, placeholder-like key material is rejected.
- Production requires an HTTPS central URL and a loopback host setting.
- Protected routes require the cached bearer token and `X-Session-Token`.
- There is no offline licence grace period: authorization ends at cached
  `valid_until`, and inactive licences fail closed.

Generate each key independently and provision it through the installer or OS secret
store. Never commit the output:

```powershell
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
([Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_'))
```

Required environment variables are `RSAP_DATABASE_KEY`,
`RSAP_FIELD_ENCRYPTION_KEY`, and `RSAP_CENTRAL_API_URL`.

## Identity and synchronization

Camera IDs are canonical client-generated UUIDs. The desktop persists the UUID
before the first central request, Agent-1 inserts that UUID as `va.cameras.id`, and
repeated identical creates use the primary key as the durable idempotency boundary.
A retry with the same UUID but different camera fields is rejected with `409`.
Analytics DTOs use that same UUID.

Outgoing events, alerts, and people counts are mapped to explicit central DTOs.
Local paths are never sent as central file identifiers. A capture with a local path
but no central file UUID fails locally as a permanent contract error.

Queue items are versioned and immutable while leased. A mutation during an active
lease creates or coalesces a successor, and dependencies ensure camera creation
completes before its analytics. States are:

```text
pending -> inflight -> succeeded
                    -> retry_wait -> inflight
                    -> dead_letter -> pending | cancelled
```

Only transient failures retry. Permanent 4xx/contract failures dead-letter
immediately; transient failures dead-letter after `RSAP_QUEUE_MAX_ATTEMPTS`.
Backoff is bounded at 300 seconds. Succeeded/cancelled rows default to seven-day
retention and dead letters to thirty days. `/sync/dead-letters` exposes redacted
failure details and administrator retry/discard operations. Retention detaches
completed dependency chains and cancels descendants of expired dead letters so
historical references cannot make the queue grow forever.

Agent-3 schedules `SyncService.flush_once()`; this foundation starts no scheduler.

## Session lifecycle

Refresh and logout share a process-local async mutation lock and a persisted session
generation. Refresh persists replacement tokens only with a generation compare-and-
swap. Logout first marks the session `revocation_pending`, immediately denies local
authorization, and then calls central logout. A temporary failure creates a durable,
bounded revocation queue item that survives restart. Central replay/revocation
responses clear local state.

## Authorization

Authorization is deny-by-default. Built-in roles grant explicit actions:

- admin/super-admin: all local actions;
- VA user: camera read/create/update/delete, analytics read/write, persons read,
  and sync read;
- staff: persons read/write only.

Central per-user grants may add matching resource actions. Licence expiry,
`max_cameras`, analytics modules, and active state are enforced in service methods.
Camera limit checks run inside the same `BEGIN IMMEDIATE` transaction as creation.

## API

Every response uses `{ "success": boolean, "data": ..., "error": ... }`, including
404, 405, validation, authorization, conflict, and internal failures.

- `/health`
- `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/me`, `/auth/license-status`
- paginated `/cameras`, `/persons`, and `/analytics/events`
- `/analytics/alerts`, `/analytics/people-count`
- `/sync/status`, `/sync/dead-letters`

Pagination uses `limit` (default 100, maximum 500) and non-negative `offset` with
stable ordering and a total count.

## Migrations

Startup applies contiguous four-digit migrations in one exclusive transaction.
Checksums, duplicate versions, unknown/future history, removed versions, and gaps
are rejected before application services start. Upgrades are forward-only.

## Docker development image

The image runs as UID 10001, exposes container port 8000, binds Uvicorn to
`0.0.0.0:8000`, and includes a `/health` healthcheck. Recursive ignore rules exclude
databases, environments, caches, validation artifacts, credentials, and key files.
Its sole writable runtime location is `/home/rsap/.rsap`; the image creates that
directory with mode `0700` and configures the database as
`/home/rsap/.rsap/local.db`. Mount persistent storage only at
`/home/rsap/.rsap` and do not place runtime data under `/app`.
Production desktop installation remains an OS-local service on `127.0.0.1:8001`.

## Development

```powershell
Set-Location apps/desktop-backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q .
```
