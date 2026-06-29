# RSAP — Architecture Summary & Agent Map

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        RSAP PLATFORM OVERVIEW                               │
├──────────────────────────┬──────────────────────────────────────────────────┤
│   CENTRAL (Cloud/On-prem)│          EDGE (VA Operator Machine)              │
│                          │                                                   │
│  ┌──────────────────┐    │   ┌──────────────────────────────────────────┐   │
│  │  Next.js Webapp  │    │   │            Tauri Desktop App             │   │
│  │  (Admin/Staff)   │    │   │         (React + TypeScript)             │   │
│  └────────┬─────────┘    │   └───────────────────┬──────────────────────┘   │
│           │              │                       │ localhost:8001            │
│  ┌────────▼─────────┐    │   ┌───────────────────▼──────────────────────┐   │
│  │   FastAPI API    │◄───┼───│         Desktop FastAPI Daemon           │   │
│  │ (webapp-backend) │ WS │   │        (desktop-backend)                 │   │
│  └──────┬───┬───────┘    │   └──────┬──────────────┬────────────────────┘   │
│         │   │            │          │ Threads       │ APScheduler            │
│  ┌──────▼┐ ┌▼────────┐   │   ┌──────▼──────┐ ┌────▼──────────────┐        │
│  │Postgres│ │  Redis  │   │   │  CV Engine  │ │  Sync Jobs        │        │
│  │  16   │ │    7    │   │   │ YOLO+DeepFace│ │ (offline queue)   │        │
│  └───────┘ └─────────┘   │   └──────┬──────┘ └────┬──────────────┘        │
│                          │          │ Frames       │ Events                  │
│  ┌───────────────────┐   │   ┌──────▼──────┐ ┌────▼──────────────┐        │
│  │   MinIO Server    │   │   │  IP Cameras │ │ SQLCipher SQLite   │        │
│  │ (File Storage)    │   │   │  NVR / RTSP │ │ (encrypted local) │        │
│  └───────────────────┘   │   └─────────────┘ └───────────────────┘        │
└──────────────────────────┴──────────────────────────────────────────────────┘
```

## Agent → Module Map

| Agent | Module | Token Estimate | Priority |
|-------|--------|---------------|----------|
| Agent-0 | Infrastructure, Docker, DB Schema | ~40k | 🔴 First |
| Agent-1 | Webapp Backend (FastAPI) | ~120k | 🔴 Phase 1 |
| Agent-2 | Webapp Frontend (Next.js) | ~100k | 🟡 Phase 1 |
| Agent-3 | Desktop Backend (FastAPI daemon) | ~130k | 🟡 Phase 2 |
| Agent-4 | Desktop Frontend (Tauri+React) | ~100k | 🟡 Phase 2 |
| Agent-5 | File Server (MinIO wrapper) | ~30k | 🟢 Phase 1 |
| Agent-6 | CV Engine Package | ~80k | 🟡 Phase 2 |
| Agent-7 | Integration & Wiring | ~50k | 🔴 Last |

## Key Design Decisions

### Why Dual-Token Auth?
Every API call carries both a standard JWT AND a server-generated UUID session token
stored in Redis, with PostgreSQL `auth.sessions` retained as a durable audit log. This enables:
- **Single-session enforcement**: a new login overwrites `session:{user_id}` and invalidates the old session immediately
- **Admin kill-switch**: deleting the Redis key forces logout on the next request
- **License expiry enforcement**: Redis key TTL matches the licence expiration
- **Fast validation**: live token checks avoid a PostgreSQL session query on every API request


### Redis Deployment Recommendation
Redis remains part of the central deployment because it provides fast session-token validation, immediate revocation, single-session enforcement, licence-aligned TTLs, and refresh-token storage. It is not required for CV inference, video streaming, APScheduler jobs, WebSockets, or the desktop offline queue.

Redis is managed automatically by Docker Compose. Normal operation requires only:
```bash
docker compose up -d
```
No manual `redis-server` startup is required.

Production controls:
- `restart: unless-stopped` for automatic recovery and boot-time restart
- AOF persistence (`--appendonly yes`) with a named `/data` volume
- Password/ACL authentication supplied through environment variables
- Internal access only through `rsap-net`; do not publish host port `6379`
- Authenticated health check using `redis-cli`
- PostgreSQL `auth.sessions` retained as the durable audit record

Recommended responsibility split:

| Component | Responsibility |
|---|---|
| Redis | Live session token, refresh token, TTL, revocation, single-session state |
| PostgreSQL | Users, licences, permissions, session audit/history |
| SQLite/SQLCipher | Desktop offline queue and local edge data |
| APScheduler | Scheduled desktop synchronization |

### Why Separate Capture/Process/Stream Threads?
The CV pipeline has three competing needs:
1. **Capture**: read frames as fast as the camera provides (25-30fps)  
2. **Analytics**: run YOLO+face-rec at a sustainable rate (10fps)
3. **Stream**: deliver smooth video to the frontend (25-30fps)

By separating these, analytics slowdowns never cause video stuttering.

### Why APScheduler + Asyncio (no external broker)?
Requirement: zero external software dependencies for sync.
APScheduler runs inside the Python process, uses asyncio-compatible scheduler,
no RabbitMQ or Celery is needed. Redis is used only by the central authentication layer, not by desktop synchronization. The offline queue is a local SQLite table.

### Why SQLCipher for Desktop?
Camera RTSP URLs contain credentials. If the local DB were unencrypted, 
any file access = credential exposure. SQLCipher encrypts the entire DB file
transparently, key derived from user password + machine fingerprint.

## File Naming Convention (MinIO)
All stored files use UUID v4 as name: `{uuid4()}.{ext}`
No original filenames. Categories via bucket + prefix path.
```
faces/          {person-uuid}.jpg
captures/       {YYYY-MM-DD}/{event-uuid}.jpg  
documents/      {category}/{doc-uuid}.pdf
```
