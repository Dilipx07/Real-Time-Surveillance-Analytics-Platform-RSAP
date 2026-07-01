# RSAP Desktop Backend Foundation

This service is the localhost FastAPI boundary and encrypted persistence layer for
the RSAP desktop application. It binds to `127.0.0.1:8001`. Camera capture,
computer-vision orchestration, frame streaming, and job scheduling are deliberately
outside this module and are added by the desktop-orchestration owner.

## Security model

- The database uses SQLCipher in every non-test environment.
- Camera URLs, person phone numbers, and cached central tokens also use
  purpose-bound AES-256-GCM field encryption.
- Both the cached central bearer token and `X-Session-Token` are required for
  protected local routes. Access-token and licence expiration are enforced.
- The service refuses non-loopback binds, plaintext production databases, and
  non-HTTPS central URLs in production.
- Secrets have no defaults and are never logged.

Provision the database and field keys independently through an installer or OS
secret store. The following PowerShell generates a URL-safe 256-bit value; run it
twice and do not commit the results:

```powershell
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
([Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_'))
```

Required environment variables:

```text
RSAP_DATABASE_KEY=<base64url-encoded 32 bytes>
RSAP_FIELD_ENCRYPTION_KEY=<different base64url-encoded 32 bytes>
RSAP_CENTRAL_API_URL=https://api.example.invalid
```

Optional settings include `RSAP_DATA_DIR`, `RSAP_DATABASE_PATH`,
`RSAP_REQUEST_TIMEOUT_SECONDS`, `RSAP_CONNECT_TIMEOUT_SECONDS`, and bounded retry
settings. `RSAP_DATABASE_DRIVER=sqlite-test` is rejected unless
`RSAP_ENVIRONMENT=test`.

## Local API

All responses use `{ "success": boolean, "data": ..., "error": ... }`.

| Route | Purpose | Auth |
|---|---|---|
| `GET /health` | Database and offline/online state | Public, loopback only |
| `POST /auth/login` | Forward login to central and cache encrypted session | Public |
| `POST /auth/refresh` | Rotate central and local access/refresh tokens | Session + refresh token |
| `POST /auth/logout` | Revoke central and clear local session | Dual token |
| `GET /auth/me` | Cached central user | Dual token |
| `GET /auth/license-status` | Cached licence state | Public, loopback only |
| `/cameras` | Transactional local camera CRUD | Dual token |
| `GET/POST /analytics/events` | Read or persist local events | Dual token |
| `POST /analytics/alerts` | Persist an alert and enqueue it | Dual token |
| `POST /analytics/people-count` | Persist a count and enqueue it | Dual token |
| `GET /persons` | Read the local person cache | Dual token |
| `GET /sync/status` | Connectivity and durable queue depth | Dual token |

## Migrations

Migrations in `app/migrations/` are ordered by their four-digit prefix. Startup
applies them under an exclusive transaction and records a SHA-256 checksum in
`schema_migrations`. A changed applied migration stops startup. Upgrades are
forward-only: encrypted edge data must be backed up before deploying a new schema;
automatic downgrades are intentionally unsupported.

## Offline and synchronization contract

Analytics events, alerts, and people counts are inserted together with their queue
record in one transaction. `SyncQueueRepository.claim()` provides exclusive,
expiring leases, and `SyncService.flush_once()` performs one bounded pass. Agent-3
may schedule that method, but this service does not start a scheduler itself.

Central batch sync endpoints are safe to retry because Agent-1 upserts client event
UUIDs. Central camera creation is not idempotent, so camera `POST` is never retried
after an uncertain network outcome. A process failure after the central create but
before the local acknowledgement still requires later reconciliation; the current
Agent-1 contract has no idempotency key or client-supplied camera UUID.

## Development and validation

```powershell
Set-Location apps/desktop-backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q .
```

The test suite uses plaintext SQLite only through the explicit `sqlite-test`
driver. It separately verifies that a real SQLCipher database cannot be opened by
the standard-library SQLite driver.
