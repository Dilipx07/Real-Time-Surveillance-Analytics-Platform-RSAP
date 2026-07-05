"""RSAP desktop-backend FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.container import Container
from app.responses import envelope, install_exception_handlers
from app.routers import analytics, auth, cameras, persons, sync


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        container = Container(resolved)
        try:
            await container.start()
        except Exception:
            await container.close()
            raise
        application.state.container = container
        try:
            yield
        finally:
            await container.close()

    application = FastAPI(
        title="RSAP Desktop API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if resolved.environment != "production" else None,
        redoc_url=None,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Session-Token"],
    )
    install_exception_handlers(application)
    application.include_router(auth.router)
    application.include_router(cameras.router)
    application.include_router(analytics.router)
    application.include_router(persons.router)
    application.include_router(sync.router)

    @application.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        container: Container = request.app.state.container
        await container.database.verify()
        return envelope({
            "status": "ok",
            "database": "ok",
            "sync": "connected" if container.connected else "offline",
            "timestamp": datetime.now(UTC),
        })

    return application


app = create_app()
