"""RSAP desktop-backend FastAPI application."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from collections.abc import Callable
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.container import Container
from app.responses import envelope, install_exception_handlers
from app.routers import analytics, auth, cameras, orchestration, persons, sync


def create_app(
    settings: Settings | None = None,
    *,
    container_factory: Callable[[Settings], Container] = Container,
) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        container = container_factory(resolved)
        try:
            await container.start()
        except asyncio.CancelledError as startup_error:
            try:
                await _settle_container_close(container)
            except BaseException as cleanup_error:
                raise startup_error from cleanup_error
            raise
        except Exception:
            await _settle_container_close(container)
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
    application.include_router(orchestration.router)

    @application.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        container: Container = request.app.state.container
        await container.database.verify()
        return envelope({
            "status": "ok",
            "database": "ok",
            "sync": "connected" if container.connected else "offline",
            "orchestration": {
                "service": container.orchestration.runtime_status(),
                **container.camera_manager.health().to_dict(),
            },
            "timestamp": datetime.now(UTC),
        })

    return application


async def _settle_container_close(container: Container) -> None:
    """Observe startup cleanup even when the lifespan waiter is cancelled."""
    cleanup_task = asyncio.create_task(
        container.close(), name="desktop-lifespan-startup-cleanup"
    )
    while True:
        try:
            await asyncio.shield(cleanup_task)
            return
        except asyncio.CancelledError:
            if cleanup_task.done():
                cleanup_task.result()
                return


app = create_app()
