# RSAP Agent Instructions

## Repository

Repository name:

`Real-Time-Surveillance-Analytics-Platform-RSAP`

The development host is Windows using PowerShell and Docker Desktop.
Docker containers use Linux images.

## Source of truth

Read these files before making changes:

- `docs/ARCHITECTURE_SUMMARY_REDIS_RECOMMENDED.md`
- `docs/agent-prompts/CODEX_MASTER_PROMPT_REDIS_RECOMMENDED.md`

## Global architecture

- Central backend: FastAPI with Python 3.12
- Central frontend: Next.js with TypeScript
- Desktop backend: FastAPI daemon on `127.0.0.1:8001`
- Desktop frontend: Tauri 2 with React and TypeScript
- Central database: PostgreSQL 16
- Desktop database: SQLCipher SQLite
- Central session authority: Redis 7
- Object storage: MinIO
- CV package: `packages/cv-engine`
- Reverse proxy: Caddy
- Container orchestration: Docker Compose

## Authentication

Protected central API requests require both:

- `Authorization: Bearer <JWT>`
- `X-Session-Token: <SESSION_TOKEN>`

Redis is the live session authority.

PostgreSQL `auth.sessions` is the durable session audit record.

## Agent operating rules

1. Inspect the repository before editing.
2. Work only inside the assigned ownership scope.
3. Preserve valid work created by other agents.
4. Do not create placeholder implementations.
5. Do not leave `TODO: implement` comments.
6. Do not return fake success responses.
7. Run validation commands before completion.
8. Fix validation failures before stopping.
9. Report changed files, commands executed, test results and blockers.
10. Commit completed work to the assigned branch.
11. Never commit `.env`, credentials, keys, tokens, databases or model files.
12. Use PowerShell-compatible commands for Windows host documentation.
13. Linux shell syntax is allowed inside Dockerfiles and Linux containers.
14. Use Docker Compose V2 syntax: `docker compose`.

## Module ownership

- `agent-0-infra`
  - repository scaffold
  - `infra/`
  - `.env.example`
  - root `.gitignore`
  - root `README.md`
  - initial Dockerfiles

- `agent-1-webapp-backend`
  - `apps/webapp-backend/`
  - central backend API contracts

- `agent-2-webapp-frontend`
  - `apps/webapp-frontend/`
  - frontend shared TypeScript types when required

- `agent-3-desktop-backend`
  - `apps/desktop-backend/`

- `agent-4-desktop-frontend`
  - `apps/desktop-frontend/`

- `agent-5-file-server`
  - `apps/file-server/`

- `agent-6-cv-engine`
  - `packages/cv-engine/`

- `agent-7-integration`
  - integration corrections
  - service wiring
  - end-to-end testing
  - startup documentation

## Dependency order

1. Agent-0
2. Agents 1, 5 and 6
3. Agents 2 and 3
4. Agent-4
5. Agent-7

Do not start Agent-7 until Agents 0 through 6 are merged.
Do not let multiple local agents edit the same worktree.
