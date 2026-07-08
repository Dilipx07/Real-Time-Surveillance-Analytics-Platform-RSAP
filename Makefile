COMPOSE=docker compose -f infra/docker-compose.yml
COMPOSE_DEV=docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml

.PHONY: dev prod stop logs db-migrate seed desktop health smoke compose-check

dev:
	$(COMPOSE_DEV) up -d

prod:
	$(COMPOSE) up -d

stop:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

db-migrate:
	cd apps/webapp-backend && alembic upgrade head

seed:
	powershell -ExecutionPolicy Bypass -File scripts/seed-admin.ps1

desktop:
	cd apps/desktop-frontend && npm run tauri dev

health:
	powershell -ExecutionPolicy Bypass -File scripts/dev-health.ps1

smoke:
	powershell -ExecutionPolicy Bypass -File scripts/e2e-smoke.ps1

compose-check:
	$(COMPOSE) config --quiet
	$(COMPOSE_DEV) config --quiet
