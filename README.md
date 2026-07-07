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

Requirements: Docker Desktop with Linux containers and Docker Compose V2.

```powershell
Copy-Item .env.example .env
# Replace every change_me value in .env before using the stack outside local development.
docker compose -f .\infra\docker-compose.yml up -d
docker compose -f .\infra\docker-compose.yml ps
```

Redis is managed by Docker Compose, is password protected, persists through AOF, and is available only to containers on `rsap-net`. It deliberately has no host port mapping.

For source-mounted application development:

```powershell
docker compose -f .\infra\docker-compose.yml -f .\infra\docker-compose.dev.yml up -d
```

Stop either stack with:

```powershell
docker compose -f .\infra\docker-compose.yml down
```

## Service URLs

| Service | Local URL | Routed hostname |
|---|---|---|
| Web application | http://localhost:3000 | https://app.rsap.local |
| Central API | http://localhost:8000 | https://api.rsap.local |
| File service | http://localhost:8002 | https://files.rsap.local |
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
docker compose -f .\infra\docker-compose.yml config
docker compose -f .\infra\docker-compose.yml build
docker compose -f .\infra\docker-compose.yml up -d postgres redis minio
docker compose -f .\infra\docker-compose.yml logs --tail 100 postgres redis minio
```

Never commit `.env`, credentials, generated databases, model files, or local storage data.

## End-user runtime workflow

Agent-7 integration scripts provide a PowerShell-friendly path for local validation:

```powershell
Copy-Item .\.env.example .\.env -Force
notepad .\.env
.\scripts\dev-up.ps1 -Build
.\scripts\seed-admin.ps1
.\scripts\dev-health.ps1 -SkipDesktop
.\scripts\e2e-smoke.ps1 -SkipDesktop
```

See `docs/RUNTIME_SMOKE_TEST.md` for the full central and desktop manual test guide.
