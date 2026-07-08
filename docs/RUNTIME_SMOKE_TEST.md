# RSAP Runtime Smoke Test

This guide validates RSAP from a fresh Windows clone with Docker Desktop, Python 3.12, Node/npm, and Rust only when building the Tauri desktop package.

## Start

```powershell
Set-Location D:\Open-CV\RSAP-Agent-7
Copy-Item .\.env.example .\.env -Force
notepad .\.env
python .\scripts\check-env.py
docker compose --env-file .\.env -f .\infra\docker-compose.yml config --quiet
docker compose --env-file .\.env -f .\infra\docker-compose.yml -f .\infra\docker-compose.dev.yml config --quiet
.\scripts\dev-up.ps1 -Build
.\scripts\seed-admin.ps1
.\scripts\dev-health.ps1 -SkipDesktop
```

`infra/docker-compose.dev.yml` is a development overlay and is validated with `infra/docker-compose.yml`; it is not intended to pass standalone compose validation.

Docker Compose commands in this repo pass the root `.env` explicitly with `--env-file .\.env`; do not copy `.env` into `infra\.env`. For local smoke testing only, simple non-placeholder credentials such as `POSTGRES_PASSWORD=postgres123`, `REDIS_PASSWORD=redis123`, `MINIO_ACCESS_KEY=rsapminio`, `MINIO_SECRET_KEY=minio12345678901`, `ADMIN_EMAIL=admin@rsap.local`, `ADMIN_PASSWORD=admin123`, `FILE_SERVER_SERVICE_TOKEN=filetoken12345678`, `LICENSE_SIGNING_SECRET=licensesecret123`, `JWT_SECRET=jwtsecret123456789012345678901234`, `AES_ENCRYPTION_KEY=12345678901234567890123456789012`, and `MINIO_PUBLIC_ENDPOINT=localhost:9000` are acceptable. Do not commit `.env` or real secrets.

The integration frontend uses the currently patched Next.js line to keep production audit clean. This is an Agent-7 integration decision and supersedes the older architecture text that mentioned Next.js 14 for the recovered central console shell.

Open:

```text
Central webapp: http://localhost:3000
Central API: http://localhost:8000/health
File server: http://localhost:8002/health
MinIO console: http://localhost:9001
```

Sign in with `ADMIN_EMAIL` and `ADMIN_PASSWORD` from `.env`. The central console must show Dashboard, Users, Licenses, Cameras, Persons, Analytics, and Settings. Empty pages should say not configured, not show fabricated data.

## Desktop Runtime

```powershell
Set-Location D:\Open-CV\RSAP-Agent-7\apps\desktop-backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e ..\..\packages\cv-engine
python -m uvicorn main:app --host 127.0.0.1 --port 8001
```

In another PowerShell window:

```powershell
Set-Location D:\Open-CV\RSAP-Agent-7\apps\desktop-frontend
npm install
npm run dev
```

Open `http://localhost:5173`, verify login or degraded/offline handling, then open the dashboard, orchestration, and sync/dead-letter screens.

## Manual End-User Flow

1. Log in to `http://localhost:3000` as the seeded admin.
2. Create a staff or VA user from Users.
3. Confirm the Licenses page lists the seeded admin license.
4. Open Cameras and verify it reports not configured until a real camera is added.
5. Open Persons and Analytics and verify empty states or live backend records.
6. Open the desktop UI and verify dashboard, orchestration, and sync screens load.
7. Run `.\scripts\e2e-smoke.ps1` from the repo root.
8. Stop services with `.\scripts\dev-down.ps1`.

## Troubleshooting

Ports in use: stop the conflicting process or change ports in `infra/docker-compose.yml`. Common ports are 3000, 5173, 8000, 8001, 8002, 9000, and 9001.

Docker Desktop not running: start Docker Desktop and wait until `docker info` succeeds.

Postgres health failed: verify `POSTGRES_PASSWORD` is set in `.env`, remove stale local volumes only when you intend to reset data, then rerun `.\scripts\dev-up.ps1`.

Redis auth failed: ensure `REDIS_PASSWORD` and `REDIS_URL` use the same password. Redis is internal-only and should not expose port 6379.

MinIO credentials wrong: `MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY` must match the container environment. If changed after first boot, recreate the MinIO volume only when resetting local storage is acceptable.

Missing Python 3.12: install Python 3.12 and verify `py -3.12 --version`.

Missing Node: install current Node LTS and verify `node --version` and `npm --version`.

Missing Rust/Tauri: install Rust with rustup before `npm run tauri build`.

`face_recognition` or dlib install failure: install CMake and C++ build tools, or run the service in Docker where native build dependencies are declared.

SQLCipher driver issue: install the native SQLCipher library expected by the desktop backend dependencies and rerun the desktop backend test suite.
