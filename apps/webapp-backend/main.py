from contextlib import asynccontextmanager
from datetime import UTC, datetime
import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import create_pool
from app.redis_client import create_redis
from app.responses import envelope, error_response
from app.routers import analytics, auth, cameras, licenses, persons, sync, users, websockets
from app.services.file_service import FileService

logger = logging.getLogger("rsap.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.db_pool = await create_pool(settings)
    app.state.redis = create_redis(settings)
    app.state.file_service = FileService(settings)
    try:
        await app.state.redis.ping()
        await app.state.file_service.create_buckets_if_not_exist()
        yield
    finally:
        await app.state.redis.aclose()
        await app.state.db_pool.close()


app = FastAPI(
    title="RSAP Webapp API",
    version="1.0.0",
    description="Central API for the Real-Time Surveillance Analytics Platform.",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "X-Session-Token"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
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


@app.get("/health", tags=["health"])
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
