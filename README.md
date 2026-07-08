# Real-Time Surveillance Analytics Platform (RSAP)

RSAP is a monorepo for a central surveillance management platform and its edge analytics clients. The central deployment combines a FastAPI API, a Next.js web application, PostgreSQL, authenticated Redis session storage, MinIO object storage, and Caddy. Desktop services and the shared computer-vision engine live in the same repository and are developed by their owning modules.

## Repository layout

```text
apps/
  webapp-backend/       Central FastAPI API
  webapp-frontend/      Central Next.js application
  desktop-backend/      Local FastAPI daemon
  file-server/          Internal MinIO wrapper
packages/
  cv-engine/            Shared computer-vision package
  shared-types/         Shared TypeScript contracts
  shared-schemas/       Shared Python schemas
infra/
  caddy/                Reverse-proxy configuration
  postgres/             Initial PostgreSQL schema
  docker-compose.yml    Production-oriented local stack
  docker-compose.dev.yml Development overrides
```

## Quick start

Requirements: Docker Desktop with Linux containers and Docker Compose V2, Python 3.12, and Node/npm for the desktop frontend.

```powershell
python .\scripts\run-all.py up
python .\scripts\run-all.py health
python .\scripts\run-all.py smoke
python .\scripts\run-all.py down
```

Linux:

```bash
python3 scripts/run-all.py up
python3 scripts/run-all.py health
python3 scripts/run-all.py smoke
python3 scripts/run-all.py down
```

The runner starts Docker central services and local desktop services, writes desktop logs and process metadata under ignored `.runtime/`, and creates a local-only `.env` if one does not exist. For local development and smoke testing only, it uses simple non-placeholder values that pass validation, such as `POSTGRES_PASSWORD=postgres123`, `REDIS_PASSWORD=redis123`, `MINIO_ACCESS_KEY=rsapminio`, `MINIO_SECRET_KEY=miniosecret123`, `ADMIN_EMAIL=admin@rsap.local`, `ADMIN_PASSWORD=admin123`, `FILE_SERVER_SERVICE_TOKEN=filetoken123456`, `LICENSE_SIGNING_SECRET=licensesecret123456`, `JWT_SECRET=jwtsecret123456789012345678901234`, `AES_ENCRYPTION_KEY=12345678901234567890123456789012`, and `MINIO_PUBLIC_ENDPOINT=localhost:9000`. Do not commit `.env` or real secrets.

Local login:

```text
admin@rsap.local
admin123
```

Redis is managed by Docker Compose, is password protected, persists through AOF, and is available only to containers on `rsap-net`. It deliberately has no host port mapping.

For source-mounted application development:

```powershell
docker compose --env-file .\.env -f .\infra\docker-compose.yml -f .\infra\docker-compose.dev.yml up -d
```

`infra/docker-compose.dev.yml` is a development overlay, not a standalone compose project. Validate it together with the base file:

```powershell
docker compose --env-file .\.env -f .\infra\docker-compose.yml config --quiet
docker compose --env-file .\.env -f .\infra\docker-compose.yml -f .\infra\docker-compose.dev.yml config --quiet
```

Stop either stack with:

```powershell
docker compose --env-file .\.env -f .\infra\docker-compose.yml down
```

## Service URLs

| Service | Local URL | Routed hostname |
|---|---|---|
| Web application | http://localhost:3000 | https://app.rsap.local |
| Central API | http://localhost:8000 | https://api.rsap.local |
| File service | http://localhost:8002 | https://files.rsap.local |
| Desktop webapp | http://127.0.0.1:1420 | — |
| Desktop API | http://127.0.0.1:8001 | — |
| MinIO API | http://localhost:9000 | — |
| MinIO console | http://localhost:9001 | https://minio.rsap.local |
| PostgreSQL | localhost:5432 | — |

The `.local` hostnames require local DNS or hosts-file entries pointing to `127.0.0.1`. Caddy issues development certificates with its internal CA; trust that CA on clients that should access the HTTPS hostnames without warnings.

## Architecture

```text
                         rsap-net (Docker internal network)

 Browser ──► Caddy ──┬──► Next.js webapp
                     ├──► FastAPI central API ──┬──► PostgreSQL 16
                     │                          ├──► Redis 7 (sessions)
                     │                          └──► MinIO (objects)
                     ├──► File service ─────────────► MinIO
                     └──► MinIO console

 Desktop/Tauri ──► Desktop FastAPI daemon ──► CV engine / SQLCipher
                              │
                              └──────── secure synchronization ──► Central API
```

Protected central API requests carry both `Authorization: Bearer <JWT>` and `X-Session-Token: <SESSION_TOKEN>`. Redis is the live session authority; PostgreSQL `auth.sessions` is the durable audit record.

## Infrastructure validation

```powershell
docker compose --env-file .\.env -f .\infra\docker-compose.yml config --quiet
docker compose --env-file .\.env -f .\infra\docker-compose.yml -f .\infra\docker-compose.dev.yml config --quiet
docker compose --env-file .\.env -f .\infra\docker-compose.yml build
docker compose --env-file .\.env -f .\infra\docker-compose.yml up -d postgres redis minio
docker compose --env-file .\.env -f .\infra\docker-compose.yml logs --tail 100 postgres redis minio
```

The integration frontend uses the currently patched Next.js line to keep production audit clean. This is an Agent-7 integration decision and supersedes the older architecture text that mentioned Next.js 14 for the recovered central console shell.

Never commit `.env`, credentials, generated databases, model files, or local storage data.

## End-user runtime workflow

Use the cross-platform runner for full central plus desktop validation:

```powershell
python .\scripts\run-all.py up --install
python .\scripts\run-all.py health
python .\scripts\run-all.py smoke
python .\scripts\run-all.py status
python .\scripts\run-all.py down
```

Linux uses the same commands with `python3 scripts/run-all.py ...`. The older PowerShell helpers in `scripts/` remain available for focused central-service work and are reused by the runner on Windows where practical. See `docs/RUNTIME_SMOKE_TEST.md` for the full central and desktop manual test guide.
