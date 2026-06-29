from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from minio.error import S3Error
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.minio_client import InvalidBucketError, ObjectNotFoundError, StorageService
from app.routers import download, manage, upload
from app.schemas import HealthResponse


def create_storage() -> StorageService:
    return StorageService.create(get_settings())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    storage = create_storage()
    await run_in_threadpool(storage.initialize)
    app.state.storage = storage
    try:
        yield
    finally:
        storage.close()


app = FastAPI(
    title="RSAP File Server",
    version="1.0.0",
    description="Private service-to-service file management API backed by MinIO.",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(upload.router)
app.include_router(download.router)
app.include_router(manage.router)


@app.exception_handler(InvalidBucketError)
async def invalid_bucket_handler(_request: Request, exc: InvalidBucketError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": f"Unknown bucket: {exc}"},
    )


@app.exception_handler(ObjectNotFoundError)
async def object_not_found_handler(_request: Request, exc: ObjectNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": f"File not found: {exc}"},
    )


@app.exception_handler(S3Error)
async def minio_error_handler(_request: Request, exc: S3Error) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": "Object storage request failed", "code": exc.code},
    )


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health(request: Request) -> HealthResponse | JSONResponse:
    storage: StorageService = request.app.state.storage
    try:
        await run_in_threadpool(storage.check_health)
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "degraded", "minio": "unavailable"},
        )
    return HealthResponse(status="ok", minio="ok")
