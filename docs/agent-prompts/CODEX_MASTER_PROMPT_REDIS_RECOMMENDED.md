# CODEX AGENT MASTER PROMPT
## Real-Time Surveillance Analytics Platform (RSAP)
### Multi-Agent Build System — Context Window: 300k tokens per agent

---

## ⚡ HOW TO USE THIS DOCUMENT

This document is split into **self-contained Agent Task Blocks**. Each block is one complete prompt you paste into a Codex agent window. Each agent owns one module. Agents do NOT depend on other agents completing first unless explicitly noted under `DEPENDS ON`.

**Agent Execution Order:**
```
Phase 0: INFRA (Agent-0)
Phase 1: WEBAPP-BACKEND (Agent-1) 
Phase 2: WEBAPP-FRONTEND (Agent-2)
Phase 3: DESKTOP-BACKEND (Agent-3)
Phase 4: DESKTOP-FRONTEND (Agent-4)
Phase 5: FILE-SERVER (Agent-5)
Phase 6: CV-ENGINE (Agent-6)
Phase 7: INTEGRATION + E2E (Agent-7)
```

---

## 🏗️ GLOBAL ARCHITECTURE REFERENCE
*(Include this block at the top of EVERY agent prompt)*

### Tech Stack Decisions

| Layer | Technology | Why |
|---|---|---|
| Webapp Backend | **FastAPI (Python 3.12)** | Async-native, OpenCV/YOLO ecosystem, websocket support built-in |
| Webapp Frontend | **Next.js 14 (TypeScript, App Router)** | SSR + React, strong RBAC patterns |
| Desktop Backend | **FastAPI (Python) running as local daemon** | Reuse Python CV stack, serve localhost |
| Desktop Frontend | **Tauri 2 + React + TypeScript** | Rust shell = lightweight, native, cross-platform |
| CV Engine | **Python + OpenCV + Ultralytics YOLOv8/v9 + DeepFace** | All open-source, GPU-accelerated |
| File Server | **MinIO (self-hosted S3-compatible)** | Open-source, S3 API, bucket policies, UUID naming |
| Database (Webapp) | **PostgreSQL 16** | JSONB for flexible RBAC, row-level security |
| Database (Desktop) | **SQLite (encrypted via SQLCipher)** | Local encrypted storage, no server needed |
| Cache/Session | **Redis 7** | Fast token validation, TTL-based license expiry, single-session enforcement |
| WebSockets | **FastAPI native WebSocket** (no external broker) | Zero external dependency |
| Sync Queue | **APScheduler + asyncio queues** | Built-in Python, no Celery/RabbitMQ |
| Auth | **JWT (access) + Custom Session Token** (dual-token) | Per spec: unique server token + JWT |
| Face Recognition | **DeepFace + face_recognition (dlib)** | Open-source, no cloud |
| Object Detection | **Ultralytics YOLOv8** | ONNX export for desktop edge inference |
| Video Streaming | **OpenCV + asyncio + WebSocket binary frames** | Pure Python, no GStreamer dependency |
| Containerization | **Docker + Docker Compose** | All services orchestrated |
| Reverse Proxy | **Caddy** | Auto HTTPS, simpler than Nginx |

### Monorepo Structure
```
rsap/
├── apps/
│   ├── webapp-backend/          # FastAPI — Central Server
│   ├── webapp-frontend/         # Next.js 14
│   ├── desktop-backend/         # FastAPI daemon (runs on VA machine)
│   ├── desktop-frontend/        # Tauri 2 + React
│   └── file-server/             # MinIO config + upload service wrapper
├── packages/
│   ├── cv-engine/               # YOLO + face-rec + analytics core (Python)
│   ├── shared-types/            # TypeScript types shared between frontends
│   └── shared-schemas/          # Pydantic models shared between backends
├── infra/
│   ├── docker-compose.yml
│   ├── docker-compose.dev.yml
│   ├── caddy/Caddyfile
│   └── postgres/init.sql
└── docs/
    └── api-contracts/           # OpenAPI specs per service
```

### Dual-Token Auth Flow (GLOBAL RULE — every agent must implement this)
```
LOGIN REQUEST
    │
    ▼
Backend validates credentials
    │
    ├── Creates JWT (short-lived, 15min, standard)
    └── Creates SESSION_TOKEN (UUID v4, stored in Redis with TTL = license expiry)
    
EVERY API CALL must carry:
    Authorization: Bearer <JWT>
    X-Session-Token: <SESSION_TOKEN>
    
Backend middleware checks BOTH tokens on every request.
If either is invalid/expired → 401.
Single-session enforcement: Redis key = `session:{user_id}` → session token; a new login overwrites the key and revokes the prior PostgreSQL audit row.
```

---

---

# AGENT-0: INFRASTRUCTURE SCAFFOLD
## Paste this entire block as Agent-0's prompt

```
You are a DevOps/Infrastructure agent. Your ONLY job is to create the full project scaffold, Docker Compose, and database initialization for the RSAP project.

## PROJECT: Real-Time Surveillance Analytics Platform (RSAP)

## YOUR DELIVERABLES

### 1. Create this exact monorepo directory structure:
rsap/
├── apps/
│   ├── webapp-backend/
│   │   ├── Dockerfile
│   │   └── requirements.txt  (placeholder)
│   ├── webapp-frontend/
│   │   └── Dockerfile
│   ├── desktop-backend/
│   │   ├── Dockerfile
│   │   └── requirements.txt  (placeholder)
│   ├── file-server/
│   │   └── Dockerfile
├── packages/
│   ├── cv-engine/
│   ├── shared-types/
│   └── shared-schemas/
├── infra/
│   ├── docker-compose.yml
│   ├── docker-compose.dev.yml
│   ├── caddy/
│   │   └── Caddyfile
│   └── postgres/
│       └── init.sql
├── .env.example
├── .gitignore
└── README.md

### 2. docker-compose.yml must include these services:
- postgres:16-alpine  (port 5432, persistent volume)
- redis:7-alpine      (internal port 6379, persistent volume, NOT publicly exposed)
- minio/minio        (ports 9000, 9001 console, persistent volume)
- webapp-backend     (build from apps/webapp-backend, port 8000)
- webapp-frontend    (build from apps/webapp-frontend, port 3000)
- file-server        (MinIO wrapper, port 8002)
- caddy              (ports 80, 443)

Add health checks for postgres, redis, and minio.
All services connected via internal Docker network `rsap-net`.
webapp-backend depends_on postgres (healthy) and redis (healthy).

**Redis production requirements:**
- Docker Compose manages Redis automatically; no manual `redis-server` startup is required.
- Use `restart: unless-stopped`.
- Enable AOF persistence with `--appendonly yes`.
- Mount a named volume at `/data`.
- Require authentication using `REDIS_PASSWORD`.
- Do not publish port `6379` to the host or internet; access Redis only through `rsap-net`.
- Use Redis only for live session and refresh-token state. PostgreSQL `auth.sessions` remains the durable audit log.


Use this production baseline for the Redis service:
```yaml
redis:
  image: redis:7-alpine
  restart: unless-stopped
  command: >
    sh -c 'exec redis-server --appendonly yes --requirepass "$${REDIS_PASSWORD}"'
  environment:
    REDIS_PASSWORD: ${REDIS_PASSWORD}
  volumes:
    - redis-data:/data
  networks:
    - rsap-net
  healthcheck:
    test: ["CMD-SHELL", "redis-cli -a '$${REDIS_PASSWORD}' ping | grep PONG"]
    interval: 10s
    timeout: 5s
    retries: 5

# Deliberately no `ports:` mapping for Redis.
```
Declare `redis-data:` under top-level `volumes:`.

### 3. Caddyfile:
- api.rsap.local → webapp-backend:8000
- app.rsap.local → webapp-frontend:3000
- files.rsap.local → file-server:8002
- minio.rsap.local → minio:9001

### 4. infra/postgres/init.sql — create these schemas and tables:

```sql
-- Schemas
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS rbac;
CREATE SCHEMA IF NOT EXISTS events;
CREATE SCHEMA IF NOT EXISTS va;
CREATE SCHEMA IF NOT EXISTS audit;

-- auth.users
CREATE TABLE auth.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    phone VARCHAR(20),
    password_hash TEXT NOT NULL,
    role VARCHAR(50) NOT NULL CHECK (role IN ('super_admin','admin','staff','va_user')),
    is_active BOOLEAN DEFAULT true,
    is_deleted BOOLEAN DEFAULT false,
    whatsapp_number VARCHAR(20),
    created_by UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- auth.sessions (durable audit mirror; Redis is the live session authority)
CREATE TABLE auth.sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    session_token TEXT NOT NULL UNIQUE,
    device_fingerprint TEXT,
    ip_address INET,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ
);

-- rbac.licenses
CREATE TABLE rbac.licenses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    license_key TEXT UNIQUE NOT NULL,
    features JSONB NOT NULL DEFAULT '{}',
    max_cameras INTEGER DEFAULT 8,
    analytics_modules JSONB DEFAULT '[]',
    valid_from TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_by UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- rbac.permissions
CREATE TABLE rbac.permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    resource VARCHAR(100) NOT NULL,
    actions TEXT[] NOT NULL,
    constraints JSONB DEFAULT '{}',
    granted_by UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- events.registered_persons
CREATE TABLE events.registered_persons (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id UUID,
    full_name VARCHAR(255) NOT NULL,
    phone VARCHAR(20) NOT NULL,
    aadhaar_last4 CHAR(4) NOT NULL,
    face_encoding BYTEA,           -- serialized numpy array
    face_image_id UUID,            -- MinIO file UUID
    registered_by UUID REFERENCES auth.users(id),
    entry_status VARCHAR(20) DEFAULT 'not_entered',
    entry_time TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- va.cameras
CREATE TABLE va.cameras (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    name VARCHAR(255) NOT NULL,
    stream_url_encrypted TEXT NOT NULL,   -- AES-256 encrypted
    stream_type VARCHAR(20) CHECK (stream_type IN ('rtsp','webcam','nvr')),
    location_label VARCHAR(255),
    analytics_config JSONB DEFAULT '{}',
    zones JSONB DEFAULT '[]',             -- polygon zone definitions
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- va.analytics_events
CREATE TABLE va.analytics_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id UUID REFERENCES va.cameras(id),
    event_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    captured_image_id UUID,
    synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- va.intrusion_alerts
CREATE TABLE va.intrusion_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    camera_id UUID REFERENCES va.cameras(id),
    zone_id TEXT,
    captured_image_id UUID,
    confidence FLOAT,
    resolved BOOLEAN DEFAULT false,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- audit.logs
CREATE TABLE audit.logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID,
    action VARCHAR(255) NOT NULL,
    resource VARCHAR(255),
    resource_id UUID,
    metadata JSONB DEFAULT '{}',
    ip_address INET,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX idx_sessions_user ON auth.sessions(user_id);
CREATE INDEX idx_sessions_token ON auth.sessions(session_token);
CREATE INDEX idx_licenses_user ON rbac.licenses(user_id);
CREATE INDEX idx_analytics_camera ON va.analytics_events(camera_id);
CREATE INDEX idx_analytics_created ON va.analytics_events(created_at DESC);
CREATE INDEX idx_persons_event ON events.registered_persons(event_id);
```

### 5. .env.example (all secrets externalized):
```
# Postgres
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=rsap
POSTGRES_USER=rsap_user
POSTGRES_PASSWORD=change_me_strong_password

# Redis — internal Docker service; never expose port 6379 publicly
REDIS_PASSWORD=change_me_strong_redis_password
REDIS_URL=redis://:change_me_strong_redis_password@redis:6379/0

# MinIO
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=change_me_minio_secret
MINIO_BUCKET_FACES=faces
MINIO_BUCKET_CAPTURES=captures
MINIO_BUCKET_DOCUMENTS=documents
MINIO_SECURE=false

# JWT
JWT_SECRET=change_me_32_char_minimum_secret_key
JWT_ALGORITHM=HS256
JWT_ACCESS_EXPIRE_MINUTES=15
JWT_REFRESH_EXPIRE_DAYS=7

# Encryption (AES-256 for camera URLs and sensitive data)
AES_ENCRYPTION_KEY=change_me_32_byte_key_exactly_32

# App
APP_ENV=development
APP_DOMAIN=rsap.local
ADMIN_EMAIL=admin@rsap.local
ADMIN_PASSWORD=change_me_admin_pass

# License
LICENSE_SIGNING_SECRET=change_me_license_signing_key
```

### 6. .gitignore:
Include: __pycache__, .env, *.pyc, node_modules, .next, dist, target (Rust), *.db, *.sqlite, venv, .venv, uploads/, *.log

### 7. README.md with:
- Project overview
- Quick start: `cp .env.example .env && docker compose up -d`
- Service URLs table
- Architecture diagram (ASCII)

Output all files with full content. Do not abbreviate any file.
```

---

## Redis Operational Decision (MANDATORY)

Redis is retained because it is the live session authority for fast token checks, immediate revocation, single-session enforcement, license-aligned TTLs, and refresh-token state. Docker Compose owns its lifecycle, so operators must not manually start Redis for normal deployments.

Production rules:
- Start the stack with `docker compose up -d`; Redis starts automatically.
- Use `restart: unless-stopped`, AOF persistence, a named volume, authentication, and an internal-only network.
- Never expose Redis port 6379 publicly.
- Keep `auth.sessions` in PostgreSQL as the durable audit trail, not the per-request live lookup store.
- Redis is not used for CV processing, desktop offline queues, APScheduler jobs, or WebSocket transport.

---

# AGENT-1: WEBAPP BACKEND
## Paste this entire block as Agent-1's prompt
## DEPENDS ON: Agent-0 (uses the DB schema and .env structure)

```
You are a backend engineer agent. Build the complete FastAPI webapp backend for RSAP.

## CONTEXT
Project: Real-Time Surveillance Analytics Platform
Your service: apps/webapp-backend/
Role: Central API server used by the Next.js webapp and synced from desktop agents.

## GLOBAL RULES (follow exactly)
- Python 3.12, FastAPI, asyncpg (async PostgreSQL), redis-py asyncio client
- Every endpoint requires dual-token auth: JWT (Bearer) + X-Session-Token header
- Single-session enforcement via Redis: key = `session:{user_id}` → session token; overwrite on login and revoke old audit row
- All sensitive data (camera URLs, Aadhaar digits) encrypted with AES-256 (cryptography lib) before DB storage
- Return consistent envelope: {"success": bool, "data": ..., "error": null | str}
- Audit log every write operation to audit.logs table
- All datetime = UTC

## DIRECTORY STRUCTURE TO CREATE
```
apps/webapp-backend/
├── main.py
├── requirements.txt
├── Dockerfile
├── alembic.ini
├── alembic/
│   └── versions/
├── app/
│   ├── __init__.py
│   ├── config.py           # pydantic-settings, loads .env
│   ├── database.py         # asyncpg pool setup
│   ├── redis_client.py     # redis.asyncio client setup
│   ├── dependencies.py     # FastAPI Depends: get_db, verify_dual_token
│   ├── encryption.py       # AES-256 encrypt/decrypt helpers
│   ├── middleware/
│   │   ├── __init__.py
│   │   ├── audit.py        # audit log middleware
│   │   └── session.py      # dual-token validation middleware
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── license.py
│   │   ├── camera.py
│   │   ├── person.py
│   │   └── analytics.py
│   ├── schemas/            # Pydantic request/response models
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── user.py
│   │   ├── license.py
│   │   ├── camera.py
│   │   ├── person.py
│   │   └── analytics.py
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── users.py
│   │   ├── licenses.py
│   │   ├── cameras.py
│   │   ├── persons.py
│   │   ├── analytics.py
│   │   ├── sync.py         # Desktop sync endpoints
│   │   └── websockets.py   # WS endpoint for desktop sync
│   └── services/
│       ├── __init__.py
│       ├── auth_service.py
│       ├── license_service.py
│       ├── person_service.py
│       ├── file_service.py  # MinIO operations
│       └── sync_service.py
```

## IMPLEMENT EACH MODULE FULLY

### app/config.py
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    postgres_host: str
    postgres_port: int = 5432
    postgres_db: str
    postgres_user: str
    postgres_password: str
    
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket_faces: str = "faces"
    minio_bucket_captures: str = "captures"
    minio_secure: bool = False
    
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 15
    jwt_refresh_expire_days: int = 7
    
    aes_encryption_key: str
    license_signing_secret: str
    app_env: str = "development"

    class Config:
        env_file = ".env"

settings = Settings()
```

### app/dependencies.py
Implement:
- `get_db()` → asyncpg connection from pool
- `get_redis()` → redis.asyncio connection
- `verify_dual_token(request)` → validates JWT AND X-Session-Token against Redis
  - Decodes JWT → extracts user_id
  - Checks Redis key `session:{user_id}` equals the X-Session-Token value
  - Fetches user from DB, checks is_active=true and not is_deleted
  - Returns CurrentUser dataclass

### app/routers/auth.py — FULL IMPLEMENTATION
Endpoints:
- POST /auth/login
  - Validate email + password (bcrypt)
  - Check user active + license not expired
  - Generate JWT (15min)
  - Generate SESSION_TOKEN = str(uuid4())
  - Read any existing Redis token, revoke its PostgreSQL audit row, then `SET session:{user_id} <token> EX <license_ttl_seconds>`
  - Insert the new session into `auth.sessions` as a durable audit record
  - Return: {access_token, session_token, token_type, user: {id, email, role, permissions}}
  
- POST /auth/logout
  - Delete Redis key `session:{user_id}`
  - Update `auth.sessions` and set `revoked_at=NOW()`
  
- POST /auth/refresh
  - Validate refresh token stored in Redis key `refresh:{user_id}` and issue a new access JWT
  - Issue new access JWT
  
- GET /auth/me
  - Return current user profile + permissions

### app/routers/users.py — FULL IMPLEMENTATION
All endpoints require verify_dual_token. Admin-only endpoints check role in ['admin','super_admin'].

- POST /users/ — Admin creates user
  - Roles allowed: staff, va_user
  - Hash password with bcrypt
  - Record created_by = current_user.id
  
- GET /users/ — Admin lists all users (paginated, filterable by role/status)

- GET /users/{user_id} — Get user detail

- PATCH /users/{user_id} — Update user (admin)

- DELETE /users/{user_id} — Soft delete (set is_deleted=true, delete Redis session key, revoke audit row)

- PATCH /users/{user_id}/toggle-active — Enable/disable user (revoke session if disabling)

- GET /users/{user_id}/permissions — Get user RBAC permissions

- POST /users/{user_id}/permissions — Grant permission

- DELETE /users/{user_id}/permissions/{perm_id} — Revoke permission

### app/routers/licenses.py — FULL IMPLEMENTATION
- POST /licenses/ — Admin creates license for user
  - Payload: {user_id, valid_from, valid_until, max_cameras, features, analytics_modules}
  - Generate license_key = HMAC-SHA256(f"{user_id}:{valid_until}", LICENSE_SIGNING_SECRET)
  - Update the active Redis session TTL if the user is already logged in
  
- GET /licenses/ — List all licenses (admin)

- GET /licenses/{license_id} — Get license detail

- GET /licenses/user/{user_id} — Get license for user

- PATCH /licenses/{license_id} — Update license (extend, modify features)

- DELETE /licenses/{license_id}/expire — Force expire license immediately
  - Set valid_until = NOW()
  - Delete Redis session key for that user and revoke the PostgreSQL audit row → kicks them out

- GET /licenses/{license_id}/verify — Desktop app calls this to verify license validity
  - Returns: {valid: bool, expires_at, features, max_cameras, analytics_modules}
  - This endpoint ONLY requires X-Session-Token (no JWT needed — for desktop login check)

### app/routers/cameras.py — FULL IMPLEMENTATION
- POST /cameras/ — Register camera
  - Encrypt stream_url with AES-256 before storing
  
- GET /cameras/ — List cameras for current user
  - Decrypt stream_url before returning
  
- GET /cameras/{camera_id}
- PATCH /cameras/{camera_id}
- DELETE /cameras/{camera_id}
- PATCH /cameras/{camera_id}/analytics-config — Update analytics config + zones

### app/routers/persons.py — FULL IMPLEMENTATION
- POST /persons/ — Register person (staff/admin)
  - Multipart: name, phone, aadhaar_last4, face_image file
  - Validate face_image has exactly one detectable face (use face_recognition lib)
  - Generate face encoding → serialize with pickle → store in DB as BYTEA
  - Upload face_image to MinIO bucket=faces, object_name=f"{uuid4()}.jpg"
  - Store face_image_id in DB
  
- GET /persons/ — List persons (paginated, searchable by name/phone)

- GET /persons/{person_id} — Get person detail + face image URL (presigned MinIO URL)

- PATCH /persons/{person_id} — Update person (name, phone, aadhaar)

- DELETE /persons/{person_id} — Delete person

- POST /persons/{person_id}/update-face — Re-upload face photo

- GET /persons/export — Export persons list as CSV (admin)

### app/routers/analytics.py — FULL IMPLEMENTATION
- GET /analytics/events — List analytics events (paginated, filterable by camera/type/date)
- GET /analytics/events/{event_id} — Get event detail
- GET /analytics/alerts — List intrusion alerts (paginated, filterable by resolved/camera)
- PATCH /analytics/alerts/{alert_id}/resolve — Mark alert resolved
- GET /analytics/dashboard — Aggregated stats: total persons, today entries, active cameras, alert counts
- GET /analytics/people-count — People count time series by camera

### app/routers/sync.py — Desktop sync receiver
- POST /sync/events — Desktop posts batch of analytics_events
  - Payload: list of analytics events
  - Upsert into va.analytics_events
  - Mark synced_at = NOW()
  
- POST /sync/alerts — Desktop posts batch of intrusion alerts

- POST /sync/people-count — Desktop posts people count snapshots

- POST /sync/heartbeat — Desktop posts alive ping with camera statuses

### app/routers/websockets.py — WebSocket for real-time sync
```python
# WS endpoint: /ws/sync/{user_id}
# Desktop connects here after login
# Receives: JSON messages with type: "event"|"alert"|"heartbeat"|"people_count"
# Sends back: JSON ack or config updates
# Auth: query param session_token= (validated on connect)
# On connect: register client in memory dict {user_id: websocket}
# On disconnect: remove from dict
# Ping/pong keepalive every 30 seconds
```

### app/services/file_service.py
- Use minio-py (open source)
- `upload_file(bucket, file_bytes, content_type, prefix="")` → returns UUID filename
- `get_presigned_url(bucket, object_name, expires=3600)` → returns URL
- `delete_file(bucket, object_name)`
- `create_buckets_if_not_exist()` — called at startup

### requirements.txt
```
fastapi==0.115.0
uvicorn[standard]==0.30.0
asyncpg==0.29.0
redis==5.0.8
pydantic-settings==2.4.0
pydantic==2.8.0
python-jose[cryptography]==3.3.0
bcrypt==4.2.0
passlib[bcrypt]==1.7.4
python-multipart==0.0.9
minio==7.2.9
cryptography==43.0.0
face-recognition==1.3.0
numpy==1.26.4
Pillow==10.4.0
python-dotenv==1.0.1
alembic==1.13.2
apscheduler==3.10.4
httpx==0.27.0
```

### Dockerfile
```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y \
    build-essential cmake libdlib-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

### main.py
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
# Import all routers
# Setup lifespan: create DB pool, Redis client, and MinIO buckets on startup

app = FastAPI(title="RSAP Webapp API", version="1.0.0")
# Include all routers with prefixes
# /api/v1/auth, /api/v1/users, /api/v1/licenses, /api/v1/cameras
# /api/v1/persons, /api/v1/analytics, /api/v1/sync, /ws
```

Output every file completely. No stubs. No "# implement here" comments. Full working code.
```

---

---

# AGENT-2: WEBAPP FRONTEND
## Paste this entire block as Agent-2's prompt
## DEPENDS ON: Agent-1 (knows API contracts)

```
You are a frontend engineer agent. Build the complete Next.js 14 webapp for RSAP.

## CONTEXT
Stack: Next.js 14 (App Router), TypeScript, Tailwind CSS, shadcn/ui, React Query (TanStack Query v5), Zustand, React Hook Form + Zod

## COLOR PALETTE (surveillance/professional dark theme)
- Background: #0A0D14 (near-black navy)
- Surface: #111827 (card background)
- Border: #1F2937
- Primary: #3B82F6 (blue)
- Danger: #EF4444
- Warning: #F59E0B
- Success: #10B981
- Text primary: #F9FAFB
- Text muted: #6B7280

## DIRECTORY STRUCTURE
```
apps/webapp-frontend/
├── package.json
├── tsconfig.json
├── tailwind.config.ts
├── next.config.ts
├── Dockerfile
├── public/
│   └── logo.svg
├── src/
│   ├── app/
│   │   ├── layout.tsx          # Root layout, dark theme
│   │   ├── page.tsx            # Redirect to /dashboard or /login
│   │   ├── (auth)/
│   │   │   └── login/
│   │   │       └── page.tsx    # Login page
│   │   └── (dashboard)/
│   │       ├── layout.tsx      # Sidebar + topbar layout
│   │       ├── dashboard/
│   │       │   └── page.tsx    # Admin dashboard overview
│   │       ├── users/
│   │       │   ├── page.tsx    # Users list
│   │       │   └── [id]/
│   │       │       └── page.tsx
│   │       ├── licenses/
│   │       │   ├── page.tsx    # License management
│   │       │   └── [id]/
│   │       │       └── page.tsx
│   │       ├── persons/
│   │       │   ├── page.tsx    # Registered persons list
│   │       │   └── new/
│   │       │       └── page.tsx # Register new person
│   │       ├── cameras/
│   │       │   └── page.tsx
│   │       ├── analytics/
│   │       │   ├── page.tsx    # Analytics events
│   │       │   └── alerts/
│   │       │       └── page.tsx
│   │       └── settings/
│   │           └── page.tsx
│   ├── components/
│   │   ├── ui/                 # shadcn components
│   │   ├── layout/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── Topbar.tsx
│   │   │   └── PageHeader.tsx
│   │   ├── auth/
│   │   │   └── LoginForm.tsx
│   │   ├── users/
│   │   │   ├── UserTable.tsx
│   │   │   ├── CreateUserModal.tsx
│   │   │   └── UserPermissionsPanel.tsx
│   │   ├── licenses/
│   │   │   ├── LicenseTable.tsx
│   │   │   ├── CreateLicenseModal.tsx
│   │   │   └── LicenseStatusBadge.tsx
│   │   ├── persons/
│   │   │   ├── PersonTable.tsx
│   │   │   ├── FaceCaptureWidget.tsx  # Webcam capture for face enrollment
│   │   │   └── PersonRegistrationForm.tsx
│   │   ├── cameras/
│   │   │   ├── CameraTable.tsx
│   │   │   └── AddCameraModal.tsx
│   │   ├── analytics/
│   │   │   ├── EventFeed.tsx
│   │   │   ├── AlertCard.tsx
│   │   │   ├── PeopleCountChart.tsx
│   │   │   └── DashboardStats.tsx
│   │   └── shared/
│   │       ├── DataTable.tsx   # Reusable paginated table
│   │       ├── ConfirmDialog.tsx
│   │       ├── StatusBadge.tsx
│   │       └── EmptyState.tsx
│   ├── hooks/
│   │   ├── useAuth.ts
│   │   ├── usePermissions.ts
│   │   └── useWebSocket.ts
│   ├── lib/
│   │   ├── api.ts              # Axios instance with interceptors
│   │   ├── queryClient.ts      # TanStack Query setup
│   │   └── utils.ts
│   ├── store/
│   │   ├── authStore.ts        # Zustand: user, tokens, logout
│   │   └── uiStore.ts          # Zustand: sidebar state, alerts
│   └── types/
│       ├── auth.ts
│       ├── user.ts
│       ├── license.ts
│       ├── person.ts
│       └── analytics.ts
```

## IMPLEMENT EACH MODULE FULLY

### src/lib/api.ts
```typescript
// Axios instance
// Base URL from env NEXT_PUBLIC_API_URL
// Request interceptor: attach Authorization: Bearer <jwt> AND X-Session-Token: <session_token>
// Response interceptor: on 401 → clear auth store → redirect /login
// Both tokens stored in Zustand authStore (persisted to localStorage)
// Consistent error extraction from {"success":false,"error":"..."} envelope
```

### src/store/authStore.ts (Zustand)
```typescript
interface AuthState {
  user: User | null
  accessToken: string | null
  sessionToken: string | null
  isAuthenticated: boolean
  login: (tokens: LoginResponse) => void
  logout: () => void
}
// Persist to localStorage with zustand/middleware persist
```

### Login Page
- Email + password form (React Hook Form + Zod)
- On success: store both tokens, redirect to /dashboard
- Show error messages from API
- Professional dark design matching palette

### Sidebar (Role-aware navigation)
Links shown depend on user.role:
- super_admin/admin: Dashboard, Users, Licenses, Persons, Cameras, Analytics, Alerts, Settings
- staff: Persons only
- va_user: Cameras, Analytics
Use RBAC from usePermissions hook that reads authStore.user.role + permissions

### Dashboard Page (/dashboard)
Fetch GET /api/v1/analytics/dashboard
Show stat cards:
- Total Registered Persons
- Today's Entries  
- Active Cameras
- Open Alerts
Below: recent alerts list (last 10) + people count chart (recharts LineChart)

### Users Page (/users)
- Table: email, role, status badge (active/inactive), license status, actions
- Create User button → modal (email, phone, password, role selector: staff | va_user)
- Toggle active/disable with confirm dialog
- Click row → /users/[id] detail page with permissions panel

### Licenses Page (/licenses)
- Table: user email, license key (truncated), valid_from, valid_until, features, status
- Create License button → modal:
  - User selector (dropdown of users without license)
  - Date pickers: valid_from, valid_until
  - Max cameras slider (1-8)
  - Feature toggles: face_recognition, intrusion_detection, people_counting, zone_analytics
- Force expire button (danger, confirm dialog)
- Extend license button → date picker modal

### Persons Page (/persons)
- Table: name, phone, aadhaar (****), entry status badge, registration date, face image thumbnail
- Register Person button → /persons/new
  - PersonRegistrationForm: name, phone, aadhaar last 4 digits
  - FaceCaptureWidget: use browser WebRTC (getUserMedia) to show webcam preview
    - "Capture" button → takes snapshot as base64 → preview shown
    - Validate only one face detected (call API endpoint to check)
    - Form submission: multipart/form-data with all fields + face image

### FaceCaptureWidget.tsx
```typescript
// Use navigator.mediaDevices.getUserMedia({video: true})
// Show live video preview in <video> element
// "Capture Photo" button → draw frame to <canvas> → canvas.toBlob() → File object
// Show captured preview with "Retake" option
// Validate image before submitting (API call or client-side face detection hint)
```

### Cameras Page (/cameras)
- Table: name, type (RTSP/Webcam/NVR), location, status, analytics config
- Add Camera modal: name, stream URL, type selector, location label
- Analytics config panel: toggle modules per camera

### Analytics Pages
- /analytics: event feed (paginated), filterable by camera/type/date, each event shows payload + thumbnail
- /analytics/alerts: alert cards with camera name, zone, captured image, timestamp, resolve button

### usePermissions hook
```typescript
// Returns helper functions:
// canAccess(resource: string, action: string): boolean
// isAdmin(): boolean
// isStaff(): boolean
// isVAUser(): boolean
// Based on user.role and user.permissions array from authStore
```

### package.json dependencies:
```json
{
  "next": "14.2.0",
  "react": "18.3.0",
  "typescript": "5.5.0",
  "@tanstack/react-query": "5.51.0",
  "zustand": "4.5.0",
  "axios": "1.7.0",
  "react-hook-form": "7.52.0",
  "zod": "3.23.0",
  "@hookform/resolvers": "3.9.0",
  "recharts": "2.12.0",
  "date-fns": "3.6.0",
  "clsx": "2.1.1",
  "tailwind-merge": "2.4.0",
  "lucide-react": "0.414.0",
  "@radix-ui/react-dialog": "1.1.1",
  "@radix-ui/react-dropdown-menu": "2.1.1",
  "@radix-ui/react-switch": "1.1.0",
  "@radix-ui/react-select": "2.1.1",
  "@radix-ui/react-toast": "1.2.1",
  "@radix-ui/react-slider": "1.2.0"
}
```

Output every file completely. No stubs. Full TypeScript code with proper typing throughout.
```

---

---

# AGENT-3: DESKTOP BACKEND
## Paste this entire block as Agent-3's prompt
## DEPENDS ON: Agent-1 (syncs to webapp backend)

```
You are a backend engineer agent. Build the Desktop Backend daemon for RSAP.

## CONTEXT
This is a FastAPI service that runs locally on the VA operator's machine (localhost:8001).
It serves the Tauri desktop frontend AND runs the CV engine.
It syncs data to the central webapp backend (apps/webapp-backend) via WebSocket + scheduled jobs.

## CRITICAL REQUIREMENTS
- NEVER block the CV pipeline with sync operations
- CV operations run in separate threads/processes (ProcessPoolExecutor)
- Sync runs in background APScheduler jobs
- SQLite encrypted with SQLCipher (pysqlcipher3)
- All device/camera data stored encrypted
- Offline-first: queue all events locally, sync when online
- WebSocket reconnection with exponential backoff

## DIRECTORY STRUCTURE
```
apps/desktop-backend/
├── main.py
├── requirements.txt
├── Dockerfile           # for dev; production = installed as OS service
├── app/
│   ├── __init__.py
│   ├── config.py        # local settings (port, db path, server URL)
│   ├── database.py      # SQLCipher encrypted SQLite setup
│   ├── encryption.py    # AES-256 for camera URLs
│   ├── dependencies.py  # local auth verify
│   ├── cv/
│   │   ├── __init__.py
│   │   ├── stream_manager.py    # manages all camera streams
│   │   ├── frame_processor.py   # per-camera frame processing pipeline
│   │   ├── detector.py          # YOLO inference wrapper
│   │   ├── face_engine.py       # face recognition engine
│   │   ├── zone_analyzer.py     # polygon zone crossing logic
│   │   ├── people_counter.py    # counting with tracking (SORT/ByteTrack)
│   │   └── intrusion_detector.py
│   ├── streaming/
│   │   ├── __init__.py
│   │   ├── ws_stream_server.py  # WebSocket server: sends JPEG frames to frontend
│   │   └── frame_buffer.py      # Thread-safe ring buffer per camera
│   ├── sync/
│   │   ├── __init__.py
│   │   ├── sync_scheduler.py    # APScheduler setup
│   │   ├── sync_jobs.py         # actual sync job functions
│   │   └── ws_client.py         # WebSocket client to central server
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── auth.py          # local login (verifies against central server license)
│   │   ├── cameras.py       # CRUD for local cameras
│   │   ├── stream.py        # start/stop streams, WebSocket frame endpoint
│   │   ├── analytics.py     # local analytics events query
│   │   └── persons.py       # local person sync cache
│   └── models/
│       └── local_models.py  # SQLAlchemy models for local SQLite
```

## IMPLEMENT EACH MODULE FULLY

### app/database.py — SQLCipher Encrypted SQLite
```python
# Use sqlalchemy with pysqlcipher3
# Database file: ~/.rsap/local.db (encrypted)
# Encryption key from local config (stored securely)
# Tables mirror the server but simplified for local use:
# - local_cameras (id, name, stream_url_encrypted, stream_type, analytics_config, zones)
# - local_analytics_events (id, camera_id, event_type, payload, captured_image_path, synced, created_at)
# - local_alerts (id, camera_id, zone_id, image_path, confidence, resolved, synced, created_at)
# - local_persons (id, server_id, face_encoding_path, name, phone, synced_at)
# - local_people_counts (id, camera_id, count_in, count_out, timestamp, synced)
# - sync_queue (id, endpoint, payload_json, attempts, created_at, last_attempted)
```

### app/cv/stream_manager.py — THE HEART OF THE SYSTEM
```python
# StreamManager: manages all active camera streams
# 
# Architecture:
# - Each camera gets its own CameraWorker (runs in separate thread)
# - CameraWorker:
#   1. OpenCV VideoCapture with retry logic (RTSP reconnect on failure)
#   2. Reads frames → puts into FrameBuffer (ring buffer, size=10)
#   3. Frame rate control: target 25fps read, process at configured rate (default 10fps for analytics)
#
# CameraWorker thread:
#   - Separate "capture thread": just reads frames as fast as possible, puts in buffer
#   - Separate "process thread": pulls from buffer, runs analytics pipeline
#   - Separate "stream thread": pulls LATEST frame for JPEG encoding → ws_stream_server

class StreamManager:
    def __init__(self):
        self.workers: Dict[str, CameraWorker] = {}
    
    async def start_camera(self, camera_id: str, config: CameraConfig)
    async def stop_camera(self, camera_id: str)
    async def get_active_cameras(self) -> List[str]
    def get_frame_buffer(self, camera_id: str) -> FrameBuffer
```

### app/cv/frame_processor.py — Analytics Pipeline
```python
# Per-camera async pipeline:
# Frame → YOLO detection → [face recognition if persons present]
#      → [zone analysis] → [people counting] → [intrusion detection]
#      → Emit event to local DB → Queue for sync
#
# Use ThreadPoolExecutor for CPU-bound YOLO inference
# Keep analytics pipeline non-blocking to main stream
# 
# Pipeline runs at max configured FPS (default 10fps for analytics)
# Stream buffer always has latest frame at native FPS
```

### app/cv/detector.py — YOLO Wrapper
```python
# Load YOLOv8 model (ultralytics)
# Model: yolov8n.pt (nano for speed) or configurable
# Target classes: person (0), car (2), motorcycle (3), bicycle (1)
# 
# def detect(frame: np.ndarray, conf_threshold=0.5) -> List[Detection]:
#   Detection: {class_id, class_name, confidence, bbox: [x1,y1,x2,y2]}
#
# Run inference in thread pool (CPU-bound)
# Cache model in memory: load once at startup
```

### app/cv/face_engine.py — Face Recognition
```python
# Load face encodings from local_persons on startup
# Sync with server periodically
#
# def recognize_faces(frame, detections) -> List[FaceMatch]:
#   - For each person detection bbox → crop face region
#   - Use face_recognition.face_encodings()  
#   - Compare against known encodings with tolerance=0.6
#   - Return: {person_id, name, confidence, bbox}
#   - Unknown face → {person_id: None, name: "Unknown"}
```

### app/cv/zone_analyzer.py
```python
# Polygon zone crossing detection
# Each camera has configured zones (polygons defined by vertex points)
#
# def analyze_zones(detections, zones, frame_shape) -> List[ZoneEvent]:
#   - For each detection: check if centroid is inside any polygon
#   - Use cv2.pointPolygonTest()
#   - Track presence per zone per object (to avoid duplicate events)
#   - Emit ZoneEnter/ZoneExit events
```

### app/cv/people_counter.py
```python
# Counting with directional line crossing
# Define counting line (configurable per camera)
# Track objects across frames with simple centroid tracking
# When object crosses line → increment IN or OUT counter
# Reset counts at configurable intervals (e.g. midnight)
#
# Use simple SORT-like tracker (implement in pure Python/numpy, no external dep):
# - Match detections frame-to-frame with Hungarian algorithm (scipy.optimize.linear_sum_assignment)
# - IoU-based matching
```

### app/streaming/ws_stream_server.py — JPEG Frame Streaming
```python
# WebSocket endpoint: /ws/stream/{camera_id}
# Frontend connects to get live video
#
# On connect:
#   - Verify local session token
#   - Get frame buffer for camera_id
#   - Start streaming loop
#
# Streaming loop:
#   while websocket connected:
#     frame = frame_buffer.get_latest()
#     if frame is not None:
#       jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])[1]
#       await websocket.send_bytes(jpeg.tobytes())
#       # Draw analytics overlays on frame before encoding
#       # Overlays: detection boxes, zone polygons, person names, counters
#     await asyncio.sleep(1/30)  # target 30fps to frontend
#
# CRITICAL: Never block frame capture with stream encoding
# Use separate asyncio task per client
```

### app/sync/ws_client.py — Central Server WebSocket Client
```python
# Maintains persistent WebSocket to central server
# URL: f"{CENTRAL_SERVER_WS_URL}/ws/sync/{user_id}?session_token={token}"
#
# Connection management:
#   - Auto-reconnect with exponential backoff (1s, 2s, 4s, 8s, max 60s)
#   - Detect network availability before reconnecting
#   - On reconnect: flush sync_queue
#
# Message types sent to server:
#   {type: "heartbeat", data: {active_cameras: [...], timestamp: ...}}
#   {type: "event", data: analytics_event}
#   {type: "alert", data: intrusion_alert}
#   {type: "people_count", data: count_snapshot}
#
# Message types received from server:
#   {type: "config_update", data: {...}}  → update local camera config
#   {type: "license_update", data: {...}} → update local license cache
```

### app/sync/sync_jobs.py — APScheduler Jobs
```python
# Setup APScheduler with AsyncIOScheduler

# Job 1: sync_pending_events — every 30 seconds
#   - Query local_analytics_events where synced=False
#   - Batch (max 100) → POST /sync/events to central server
#   - Mark as synced

# Job 2: sync_pending_alerts — every 30 seconds  
#   - Same pattern for alerts

# Job 3: sync_people_counts — every 60 seconds

# Job 4: check_license — every 5 minutes
#   - GET /licenses/verify from central server
#   - If expired or not found: set local license state = expired
#   - Desktop frontend polls local /auth/license-status

# Job 5: sync_persons — every 10 minutes
#   - GET /persons from central server
#   - Update local face encodings

# Job 6: connectivity_check — every 10 seconds
#   - Simple HTTP HEAD to central server
#   - Update connectivity state
#   - If just reconnected → trigger immediate sync of all queued items
```

### app/routers/auth.py — LOCAL AUTH
```python
# POST /auth/login
#   - Payload: {email, password}
#   - Forward credentials to central server POST /auth/login
#   - If success: cache tokens locally (encrypted file ~/.rsap/session.enc)
#   - Check license validity from response
#   - Return same response to frontend

# GET /auth/license-status
#   - Return cached license state + expiry
#   - If expired: return {valid: false, message: "License expired", contact_whatsapp: "...", contact_email: "..."}

# POST /auth/logout
#   - Clear local session cache
#   - Notify central server

# Enforce single session: store current token, reject if token mismatch
```

### requirements.txt
```
fastapi==0.115.0
uvicorn[standard]==0.30.0
sqlalchemy==2.0.32
pysqlcipher3==1.2.0
ultralytics==8.2.82
opencv-python-headless==4.10.0.84
face-recognition==1.3.0
numpy==1.26.4
Pillow==10.4.0
scipy==1.14.0
cryptography==43.0.0
aiohttp==3.10.0
aiofiles==24.1.0
apscheduler==3.10.4
websockets==13.0
pydantic-settings==2.4.0
python-dotenv==1.0.1
httpx==0.27.0
```

Output every file completely. Full working Python code. No stubs.
```

---

---

# AGENT-4: DESKTOP FRONTEND
## Paste this entire block as Agent-4's prompt
## DEPENDS ON: Agent-3 (connects to desktop-backend on localhost:8001)

```
You are a frontend/Tauri engineer agent. Build the Tauri 2 + React desktop application for RSAP.

## CONTEXT
Stack: Tauri 2 (Rust), React 18, TypeScript, Vite, Tailwind CSS, Zustand, TanStack Query
Connects to: desktop-backend on http://localhost:8001 (NOT the central server)
Purpose: Video analytics dashboard for VA users and staff

## DIRECTORY STRUCTURE
```
apps/desktop-frontend/
├── src-tauri/
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   └── src/
│       ├── main.rs
│       └── lib.rs          # Tauri commands: system info, encrypted storage read
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── pages/
│   │   ├── Login.tsx           # License check login
│   │   ├── LicenseExpired.tsx  # License expired/not found screen
│   │   ├── Dashboard.tsx       # Main camera grid + analytics
│   │   ├── CameraSetup.tsx     # Add/manage cameras
│   │   ├── Analytics.tsx       # Events and alerts view
│   │   ├── PersonRegister.tsx  # Staff: register persons
│   │   └── Settings.tsx
│   ├── components/
│   │   ├── CameraGrid.tsx          # 1-8 camera grid layout
│   │   ├── CameraCell.tsx          # Single camera view with WS stream
│   │   ├── VideoCanvas.tsx         # Renders JPEG frames from WS to canvas
│   │   ├── AnalyticsOverlay.tsx    # Draws detection boxes on canvas
│   │   ├── ZoneEditor.tsx          # Draw polygon zones on camera frame
│   │   ├── AnalyticsSidebar.tsx    # Analytics config per camera
│   │   ├── AlertBanner.tsx         # Intrusion alert notification
│   │   ├── PeopleCounter.tsx       # Live people count display
│   │   ├── LicenseStatusBar.tsx    # Shows license expiry in header
│   │   └── SyncIndicator.tsx       # Shows sync status (online/offline)
│   ├── store/
│   │   ├── authStore.ts
│   │   ├── cameraStore.ts
│   │   └── analyticsStore.ts
│   ├── hooks/
│   │   ├── useVideoStream.ts    # WS connection for JPEG frames
│   │   ├── useLicenseCheck.ts  # Polls license status
│   │   └── useSyncStatus.ts    # Monitors sync health
│   └── lib/
│       ├── api.ts               # Axios to localhost:8001
│       └── utils.ts
├── package.json
└── vite.config.ts
```

## IMPLEMENT EACH MODULE FULLY

### pages/Login.tsx
Dark professional login screen:
- RSAP logo/branding
- Email + password form
- On submit: POST localhost:8001/auth/login
- Before showing form: GET /auth/license-status
  - If no stored credentials → show login form
  - If credentials exist + license valid → auto-login → Dashboard
- Loading state: "Verifying license..."
- Error states with appropriate messages

### pages/LicenseExpired.tsx
Full-screen branded page shown when license expires or user deactivated:
```
┌────────────────────────────────────────────────────┐
│                  ⚠ ACCESS RESTRICTED                │
│                                                     │
│   Your license has expired or account is inactive   │
│                                                     │
│   Contact your administrator:                       │
│   [WhatsApp Button]  [Email Button]                 │
│                                                     │
│   License expired on: [date]                        │
└────────────────────────────────────────────────────┘
```
- WhatsApp button: opens `https://wa.me/{whatsapp_number}`
- Email button: opens `mailto:{admin_email}`
- Auto-logout, clear local session

### pages/Dashboard.tsx — THE MAIN UI
Layout:
```
┌─ Topbar: RSAP | [SyncIndicator] [LicenseStatusBar] [Settings] [Logout] ─┐
│                                                                          │
│ ┌─ Left panel (200px) ─────────┐ ┌─ Camera Grid (flex-fill) ──────────┐│
│ │ Camera list                  │ │                                     ││
│ │ ○ Cam 1 [active]             │ │  ┌──────┐ ┌──────┐ ┌──────┐       ││
│ │ ○ Cam 2 [active]             │ │  │      │ │      │ │      │       ││
│ │ ○ Cam 3 [idle]               │ │  │      │ │      │ │      │       ││
│ │                              │ │  └──────┘ └──────┘ └──────┘       ││
│ │ [+ Add Camera]               │ │                                     ││
│ │                              │ │  ┌──────┐ ┌──────┐                 ││
│ │ ─────────────                │ │  │      │ │      │                 ││
│ │ Selected: Cam 1              │ │  │      │ │      │                 ││
│ │ Analytics:                   │ │  └──────┘ └──────┘                 ││
│ │ ☑ Object Detection           │ │                                     ││
│ │ ☑ Face Recognition           │ │                                     ││
│ │ ☑ People Count: 47 in/12 out │ │                                     ││
│ │ ☑ Zone Analytics             │ │                                     ││
│ │ [Edit Zones]                 │ └────────────────────────────────────┘│
│ └──────────────────────────────┘                                        │
│                                                                          │
│ ─── Recent Alerts ────────────────────────────────────────────────────  │
│ [Alert cards scrollable row]                                             │
└──────────────────────────────────────────────────────────────────────────┘
```

Camera grid: 1 cam = full width, 2 = 2-col, 3-4 = 2x2, 5-6 = 2x3, 7-8 = 2x4
Each CameraCell selectable → shows analytics config in sidebar

### components/CameraCell.tsx + hooks/useVideoStream.ts
```typescript
// useVideoStream hook:
// Connects to ws://localhost:8001/ws/stream/{camera_id}
// Receives binary messages (JPEG bytes)
// Creates Blob URL from each frame → updates ref
// Maintains connection with auto-reconnect on close

// VideoCanvas component:
// <canvas> element, ref to canvas context
// On each frame received:
//   - createImageBitmap(blob) → drawImage to canvas
//   - Smooth: requestAnimationFrame for rendering
// Shows "Connecting..." overlay when WS not ready
// Shows "No Signal" with camera icon when stream fails

// AnalyticsOverlay:
// Receives latest analytics data via separate WS or polling
// Draws on canvas AFTER video frame:
//   - Green boxes: detected persons with name labels
//   - Yellow boxes: vehicles
//   - Colored polygon outlines: zones
//   - Counter display: ↑ 47 IN | ↓ 12 OUT
```

### components/ZoneEditor.tsx — Polygon Zone Drawing Tool
```typescript
// Shown when user clicks "Edit Zones" for a camera
// Modal with canvas showing frozen frame from camera
// User clicks to add polygon vertices
// Double-click to close polygon
// Each zone has: name, color, alert_on_entry boolean
// Output: array of {name, color, alert_on_entry, vertices: [{x,y},...]}
// Save button → PUT /cameras/{id}/analytics-config
```

### components/SyncIndicator.tsx
```typescript
// Small indicator in topbar
// States:
//   ● Online — syncing to server
//   ○ Offline — queued locally (shows queue count)
//   ↻ Syncing...
// Polls GET /sync/status every 10 seconds
```

### pages/PersonRegister.tsx (Staff role only)
- Same form as webapp: name, phone, aadhaar last 4
- Face capture using Tauri camera API or WebRTC via <video>
- POST to localhost:8001/persons/register
- Success: show confirmation, clear form

### src-tauri/src/main.rs
```rust
// Tauri 2 app
// Commands to expose:
// - get_machine_id() → unique machine fingerprint (MAC + CPU serial hash)
// - read_encrypted_config() → read ~/.rsap/session.enc
// - write_encrypted_config(data: String) → write to ~/.rsap/session.enc
// - open_url(url: String) → shell::open for WhatsApp/Email links
// Window: title "RSAP — Surveillance Analytics", 1600x900, min 1280x720
// Single window, no default menu bar
```

### Tauri Configuration (tauri.conf.json)
```json
{
  "productName": "RSAP",
  "version": "1.0.0",
  "allowlist": {
    "all": false,
    "shell": {"open": true},
    "http": {"all": true, "request": true},
    "fs": {"all": true, "scope": ["$HOME/.rsap/**"]}
  },
  "windows": [{"label": "main", "title": "RSAP", "width": 1600, "height": 900}]
}
```

### Tailwind color config for dark surveillance theme
- Same palette as webapp but slightly different for desktop feel
- Custom camera cell border colors: active=blue, alert=red, idle=gray

### package.json
```json
{
  "@tauri-apps/api": "2.0.0",
  "@tauri-apps/cli": "2.0.0",
  "react": "18.3.0",
  "typescript": "5.5.0",
  "vite": "5.4.0",
  "@vitejs/plugin-react": "4.3.0",
  "@tanstack/react-query": "5.51.0",
  "zustand": "4.5.0",
  "axios": "1.7.0",
  "tailwindcss": "3.4.0",
  "lucide-react": "0.414.0"
}
```

Output every file completely. Full TypeScript + React code. No stubs.
```

---

---

# AGENT-5: FILE SERVER
## Paste this entire block as Agent-5's prompt

```
You are a backend engineer agent. Build the File Server service for RSAP.

## CONTEXT
Stack: FastAPI (Python) + MinIO Python SDK
Purpose: Unified file management layer on top of MinIO
Buckets: faces, captures, documents
All file names: UUID v4 (no original filenames stored)

## DIRECTORY STRUCTURE
```
apps/file-server/
├── main.py
├── requirements.txt
├── Dockerfile
├── app/
│   ├── config.py
│   ├── minio_client.py
│   ├── routers/
│   │   ├── upload.py
│   │   ├── download.py
│   │   └── manage.py
│   └── schemas.py
```

## IMPLEMENT FULLY

### Categories and Buckets:
```
faces/          → person face enrollment images
  └── {uuid}.jpg

captures/       → analytics capture images (intrusion, face match)
  └── {date}/   → partitioned by date for easy archival
      └── {uuid}.jpg

documents/      → other documents
  └── {category}/
      └── {uuid}.{ext}
```

### app/minio_client.py
```python
# MinIO client setup
# Startup: create buckets if not exist with correct policies
# faces bucket: private (no public access)
# captures bucket: private
# documents bucket: private
# Set lifecycle policy: captures older than 90 days auto-delete
```

### Routers - Upload
- POST /upload/face → upload face image, return {file_id: UUID, url: presigned}
- POST /upload/capture → upload capture image, return {file_id: UUID}
- POST /upload/document/{category} → upload document, return {file_id: UUID}
- All uploads: validate file size < 10MB, validate mime type

### Routers - Download
- GET /files/{bucket}/{file_id} → redirect to presigned URL (valid 1 hour)
- GET /files/{bucket}/{file_id}/presigned?expires=3600 → return presigned URL JSON

### Routers - Manage  
- DELETE /files/{bucket}/{file_id} → delete file
- GET /files/{bucket} → list files with pagination
- POST /files/batch-delete → delete multiple files by IDs

### Auth
Every endpoint requires X-Service-Token header (internal service-to-service token, not user JWT).
Token configured via env FILE_SERVER_SERVICE_TOKEN.
This service is NOT directly user-facing.

### requirements.txt
fastapi==0.115.0
uvicorn[standard]==0.30.0
minio==7.2.9
python-multipart==0.0.9
pydantic-settings==2.4.0
aiofiles==24.1.0
Pillow==10.4.0

Output all files completely.
```

---

---

# AGENT-6: CV ENGINE PACKAGE
## Paste this entire block as Agent-6's prompt
## This is the shareable CV core used by desktop-backend

```
You are a computer vision engineer agent. Build the core CV engine package for RSAP.

## CONTEXT
This is packages/cv-engine/ — a Python package imported by apps/desktop-backend.
It must be installable via pip install -e ./packages/cv-engine

## REQUIREMENTS
- Buttery smooth streaming at 25-30fps to frontend
- Analytics at 10fps (configurable) to avoid overloading
- All CPU-intensive work in ThreadPoolExecutor
- Zero frame drops on stream path
- Graceful degradation if GPU not available (fall back to CPU)

## DIRECTORY STRUCTURE
```
packages/cv-engine/
├── setup.py
├── README.md
├── cv_engine/
│   ├── __init__.py
│   ├── config.py           # CVConfig dataclass
│   ├── models/
│   │   ├── __init__.py
│   │   ├── detector.py     # YOLOv8 wrapper
│   │   ├── face_engine.py  # face_recognition wrapper
│   │   └── tracker.py      # Simple SORT tracker (pure numpy/scipy)
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── frame_pipeline.py   # Main pipeline orchestrator
│   │   ├── zone_analyzer.py
│   │   ├── people_counter.py
│   │   └── intrusion_detector.py
│   ├── streaming/
│   │   ├── __init__.py
│   │   ├── capture.py      # OpenCV capture with reconnect
│   │   └── buffer.py       # Thread-safe ring buffer
│   └── utils/
│       ├── __init__.py
│       ├── drawing.py      # cv2 drawing helpers for overlays
│       └── image_utils.py  # encode/decode, resize helpers
```

## KEY IMPLEMENTATIONS

### cv_engine/streaming/capture.py
```python
class ResilientCapture:
    """
    OpenCV VideoCapture with automatic reconnection.
    Works with: RTSP URLs, webcam indices, file paths
    """
    def __init__(self, source: str | int, reconnect_delay: float = 2.0):
        self.source = source
        self.reconnect_delay = reconnect_delay
        self._cap: cv2.VideoCapture | None = None
        self._lock = threading.Lock()
    
    def read(self) -> tuple[bool, np.ndarray | None]:
        """Thread-safe frame read with auto-reconnect"""
        # Try to read frame
        # If fails: reconnect with delay
        # Return (success, frame)
    
    def _connect(self):
        """Connect/reconnect to source"""
        # For RTSP: set FFMPEG backend options for low latency
        # cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'H264'))
        # For webcam: set resolution and FPS
```

### cv_engine/streaming/buffer.py
```python
class FrameBuffer:
    """Thread-safe ring buffer for frames"""
    def __init__(self, maxsize: int = 5):
        self._frames: deque = deque(maxlen=maxsize)
        self._lock = threading.Lock()
        self._event = threading.Event()
    
    def put(self, frame: np.ndarray):
        with self._lock:
            self._frames.append(frame.copy())
            self._event.set()
    
    def get_latest(self) -> np.ndarray | None:
        """Always returns most recent frame"""
        with self._lock:
            if self._frames:
                return self._frames[-1]
            return None
    
    def wait_for_frame(self, timeout: float = 1.0) -> np.ndarray | None:
        self._event.wait(timeout)
        self._event.clear()
        return self.get_latest()
```

### cv_engine/models/tracker.py — SORT Tracker (pure Python)
```python
# Implement Simple Online and Realtime Tracking (SORT)
# Using Kalman Filter + Hungarian Algorithm
# Dependencies: numpy, scipy only
# 
# class KalmanBoxTracker:
#   State: [x, y, s, r, vx, vy, vs] (center x,y, scale, ratio, velocities)
#   Predict next position
#   Update with new detection
#
# class Sort:
#   def update(detections: np.ndarray) -> np.ndarray:
#     - detections: [[x1,y1,x2,y2,score], ...]
#     - Returns: [[x1,y1,x2,y2,track_id], ...]
#   Uses IoU-based Hungarian matching
#   Max age for lost tracks: 3 frames
#   Min hits to confirm track: 2 frames
```

### cv_engine/pipeline/frame_pipeline.py
```python
class FramePipeline:
    """
    Orchestrates all analytics for a single camera.
    Called by desktop-backend's frame_processor.
    
    Threading model:
    - Runs in ThreadPoolExecutor worker
    - Results emitted via asyncio callback
    """
    def __init__(self, config: CVConfig, event_callback: Callable):
        self.detector = YOLODetector(config.model_path)
        self.face_engine = FaceEngine() if config.face_recognition else None
        self.tracker = Sort()
        self.zone_analyzer = ZoneAnalyzer(config.zones)
        self.people_counter = PeopleCounter(config.counting_line)
        self.intrusion_detector = IntrusionDetector(config.intrusion_zones)
        self.event_callback = event_callback
        self._frame_count = 0
    
    def process(self, frame: np.ndarray, timestamp: datetime) -> AnalyticsResult:
        """
        Full pipeline for one frame.
        Returns AnalyticsResult with all detections and events.
        """
        # 1. YOLO detection
        detections = self.detector.detect(frame)
        
        # 2. Tracking
        tracked = self.tracker.update(detections_to_array(detections))
        
        # 3. Face recognition (only every 5th frame to reduce load)
        if self.face_engine and self._frame_count % 5 == 0:
            face_matches = self.face_engine.recognize(frame, detections)
        
        # 4. Zone analysis
        zone_events = self.zone_analyzer.analyze(tracked, frame.shape)
        
        # 5. People counting
        count_update = self.people_counter.update(tracked, frame.shape)
        
        # 6. Intrusion detection
        intrusions = self.intrusion_detector.check(tracked, zone_events)
        
        # 7. Emit events via callback
        if zone_events or intrusions:
            self.event_callback(AnalyticsEvent(...))
        
        self._frame_count += 1
        
        return AnalyticsResult(
            detections=detections,
            tracked=tracked,
            face_matches=face_matches,
            zone_events=zone_events,
            people_count=count_update,
            intrusions=intrusions,
            overlay_data=self._build_overlay(...)
        )
```

### cv_engine/utils/drawing.py
```python
# Overlay drawing on frames for stream preview
# draw_detections(frame, detections, colors)
# draw_tracks(frame, tracked_objects)
# draw_zones(frame, zones)
# draw_people_count(frame, count_in, count_out)
# draw_face_labels(frame, face_matches)
# All drawing NEVER mutates original frame: work on frame.copy()
```

### setup.py
```python
setup(
    name='cv-engine',
    version='1.0.0',
    packages=find_packages(),
    install_requires=[
        'opencv-python-headless>=4.10.0',
        'ultralytics>=8.2.0',
        'face-recognition>=1.3.0',
        'numpy>=1.26.0',
        'scipy>=1.14.0',
        'Pillow>=10.0.0',
    ]
)
```

Output all files with complete implementations. No stubs.
```

---

---

# AGENT-7: INTEGRATION & WIRING
## Paste this entire block as Agent-7's prompt
## DEPENDS ON: All previous agents

```
You are an integration engineer agent. Your job is to wire everything together, create startup scripts, and ensure end-to-end connectivity.

## YOUR DELIVERABLES

### 1. Root-level Makefile with targets:
- make dev           → docker compose -f infra/docker-compose.dev.yml up -d
- make prod          → docker compose -f infra/docker-compose.yml up -d  
- make stop          → docker compose down
- make logs          → docker compose logs -f
- make db-migrate    → run alembic migrations
- make seed          → create default admin user
- make desktop       → npm run tauri dev in apps/desktop-frontend/

### 2. apps/webapp-backend/scripts/seed.py
Create default admin user:
- Email from env ADMIN_EMAIL
- Password from env ADMIN_PASSWORD
- Role: super_admin
- Create a default license (valid 1 year)
- Idempotent: skip if admin already exists

### 3. Desktop Installer Script (scripts/install-desktop.sh)
- Install desktop-backend as systemd service (Linux) or LaunchAgent (Mac) or Windows Service
- Service name: rsap-desktop-backend
- Auto-start on boot
- Restart on failure
- Log to ~/.rsap/logs/

### 4. apps/webapp-backend/app/routers/auth.py — Add the single-session enforcement
```python
# On every login:
# On every login:
# 1. Read the existing Redis value for session:{user_id}
# 2. If present, mark the matching PostgreSQL audit session as revoked
# 3. Create a new session_token and store it with SET EX using the license TTL
# 4. Insert the new session into auth.sessions as an audit record
# Overwriting the Redis key invalidates the previous session immediately
```

### 5. End-to-end connectivity test script: scripts/test_e2e.py
```python
# Tests:
# 1. Can connect to PostgreSQL
# 2. Can connect to Redis using authentication
# 3. Can connect to MinIO
# 4. Admin login works (POST /api/v1/auth/login)
# 5. Create user works
# 6. Create license works
# 7. Register person works (with dummy face image)
# 8. Desktop sync endpoint works
# 9. File upload works
# Print: PASS/FAIL for each test
```

### 6. Nginx/Caddy CORS configuration
Webapp frontend at :3000 can call webapp backend at :8000.
Desktop frontend (Tauri) can call desktop backend at localhost:8001.
No CORS issues.

### 7. Environment setup verification script: scripts/check-env.py
- Verify all required env vars are set
- Check DB connection
- Check authenticated Redis connection with `PING`
- Print clear error if anything is misconfigured

### 8. apps/webapp-backend/app/middleware/session.py — FULL DUAL TOKEN MIDDLEWARE
```python
class DualTokenMiddleware(BaseHTTPMiddleware):
    EXEMPT_PATHS = ["/api/v1/auth/login", "/health", "/docs", "/openapi.json"]
    
    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)
        
        # 1. Extract JWT from Authorization header
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse({"success": False, "error": "Missing token"}, status_code=401)
        
        jwt_token = auth_header.split(" ")[1]
        
        # 2. Extract session token from X-Session-Token header
        session_token = request.headers.get("X-Session-Token")
        if not session_token:
            return JSONResponse({"success": False, "error": "Missing session token"}, status_code=401)
        
        try:
            # 3. Decode JWT
            payload = jwt.decode(jwt_token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            user_id = payload.get("sub")
            
            # 4. Verify live session token in Redis
            stored_token = await request.app.state.redis.get(f"session:{user_id}")
            if stored_token != session_token:
                return JSONResponse({"success": False, "error": "Session invalid"}, status_code=401)
            
            # 5. Attach user_id to request state
            request.state.user_id = user_id
            
        except JWTError:
            return JSONResponse({"success": False, "error": "Token invalid"}, status_code=401)
        
        return await call_next(request)
```

### 9. Complete docker-compose.dev.yml with hot-reload
- webapp-backend: mount source as volume, uvicorn --reload
- webapp-frontend: mount source, next dev
- All other services same as prod

### 10. Health check endpoints
Add to webapp-backend:
- GET /health → {"status": "ok", "db": "ok", "redis": "ok", "minio": "ok", "timestamp": "..."}

Add to desktop-backend:  
- GET /health → {"status": "ok", "cv_engine": "ok", "sync": "connected|offline", "timestamp": "..."}

Output all files completely.
```

---

## 📋 AGENT EXECUTION CHECKLIST

Before starting any agent, verify:
- [ ] Agent-0 must run FIRST — creates scaffold all others depend on
- [ ] Each agent gets its own Codex window
- [ ] Paste the `## GLOBAL ARCHITECTURE REFERENCE` section + the specific agent block together
- [ ] After each agent: review output, run linting/type checks
- [ ] Agent-7 runs LAST — integration wiring

## 🔒 SECURITY CHECKLIST (verify after all agents complete)
- [ ] All passwords bcrypt-hashed (never plain text)
- [ ] All camera URLs AES-256 encrypted in DB
- [ ] All tokens double-validated (JWT + session)
- [ ] Single session enforced through Redis key overwrite and audit-row revocation
- [ ] Aadhaar digits AES-256 encrypted
- [ ] SQLite database encrypted (SQLCipher)
- [ ] MinIO buckets private (no public access)
- [ ] Service-to-service auth (FILE_SERVER_SERVICE_TOKEN)
- [ ] No secrets in code (all from env)
- [ ] Audit logs on all write operations

## ⚡ PERFORMANCE CHECKLIST
- [ ] Frame capture thread separate from analytics thread
- [ ] Frame delivery to frontend never blocked by analytics
- [ ] YOLO inference in ThreadPoolExecutor
- [ ] Sync jobs never block CV pipeline
- [ ] Redis handles live session validation; PostgreSQL is not queried for every session-token comparison
- [ ] JPEG quality 75 for stream (balance quality vs bandwidth)
- [ ] Analytics runs at 10fps, stream delivers at 25-30fps
- [ ] Ring buffer ensures latest frame always available

---
*RSAP — Real-Time Surveillance Analytics Platform | Codex Multi-Agent Build System*
*Generated for: Events Manager Surveillance Use Case*
*All tools: 100% Open Source*
