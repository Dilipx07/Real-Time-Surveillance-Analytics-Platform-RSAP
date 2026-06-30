from contextlib import asynccontextmanager
from datetime import UTC, datetime
import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.database import create_pool
from app.redis_client import create_redis
from app.responses import STANDARD_ERROR_RESPONSES, SuccessEnvelope, envelope, error_response
from app.routers import analytics, auth, cameras, licenses, persons, sync, users, websockets
from app.services.file_service import FileService
from app.services.cleanup_service import external_cleanup_worker
from app.services.session_service import session_outbox_worker
from app.services.sync_service import manager

logger = logging.getLogger("rsap.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.logger = logger
    app.state.shutdown_event = asyncio.Event()
    app.state.background_tasks = []
    db_pool = redis = file_service = None
    try:
        db_pool = app.state.db_pool = await create_pool(settings)
        redis = app.state.redis = create_redis(settings)
        file_service = app.state.file_service = FileService(settings)
        await redis.ping()
        await file_service.create_buckets_if_not_exist()
        app.state.background_tasks = [
            asyncio.create_task(session_outbox_worker(app, app.state.shutdown_event)),
            asyncio.create_task(external_cleanup_worker(app, app.state.shutdown_event)),
        ]
        yield
    finally:
        app.state.shutdown_event.set()
        tasks = app.state.background_tasks
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await manager.close_all()
        except Exception:
            logger.exception("WebSocket shutdown failed")
        if file_service is not None:
            try:
                await file_service.close()
            except Exception:
                logger.exception("MinIO client shutdown failed")
        if redis is not None:
            try:
                await redis.aclose()
            except Exception:
                logger.exception("Redis shutdown failed")
        if db_pool is not None:
            try:
                await db_pool.close()
            except Exception:
                logger.exception("PostgreSQL pool shutdown failed")


app = FastAPI(
    title="RSAP Webapp API",
    version="1.0.0",
    description="Central API for the Real-Time Surveillance Analytics Platform.",
    lifespan=lifespan,
    responses=STANDARD_ERROR_RESPONSES,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "X-Session-Token"],
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return error_response(str(exc.detail), exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = []
    for item in exc.errors():
        path = ".".join(str(part) for part in item["loc"])
        errors.append(f"{path}: {item['msg']}")
    return error_response("; ".join(errors), 422)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled request error", exc_info=exc)
    return error_response("Internal server error", 500)


@app.get("/health", tags=["health"], response_model=SuccessEnvelope)
async def health(request: Request):
    checks = {"db": "error", "redis": "error", "minio": "error"}
    try:
        async with request.app.state.db_pool.acquire() as db:
            checks["db"] = "ok" if await db.fetchval("SELECT 1") == 1 else "error"
    except Exception:
        pass
    try:
        checks["redis"] = "ok" if await request.app.state.redis.ping() else "error"
    except Exception:
        pass
    checks["minio"] = "ok" if await request.app.state.file_service.health() else "error"
    state = "ok" if all(value == "ok" for value in checks.values()) else "degraded"
    body = envelope({"status": state, **checks, "timestamp": datetime.now(UTC)})
    return JSONResponse(content=jsonable_encoder(body), status_code=200 if state == "ok" else 503)


app.include_router(auth.router, prefix="/api/v1/auth", tags=["authentication"])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(licenses.router, prefix="/api/v1/licenses", tags=["licenses"])
app.include_router(cameras.router, prefix="/api/v1/cameras", tags=["cameras"])
app.include_router(persons.router, prefix="/api/v1/persons", tags=["registered persons"])
app.include_router(analytics.router, prefix="/api/v1/analytics", tags=["analytics"])
app.include_router(sync.router, prefix="/api/v1/sync", tags=["desktop synchronization"])
app.include_router(websockets.router, prefix="/ws", tags=["websocket synchronization"])
