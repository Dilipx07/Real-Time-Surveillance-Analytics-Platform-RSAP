import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from minio.error import S3Error
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.errors import STATUS_ERROR_CODES, error_response
from app.minio_client import (
    InvalidBucketError,
    ObjectNotFoundError,
    StorageService,
    StorageUnavailableError,
)
from app.routers import download, manage, upload
from app.schemas import ErrorResponse, HealthResponse


logger = logging.getLogger("rsap.file_server")
ERROR_RESPONSES = {
    code: {"model": ErrorResponse, "description": description}
    for code, description in {
        400: "Invalid request",
        401: "Authentication failed",
        404: "Resource not found",
        413: "Upload too large",
        415: "Unsupported media type",
        422: "Validation failed",
        500: "Internal error",
        502: "Object storage error",
        503: "Object storage unavailable",
    }.items()
}


def create_storage() -> StorageService:
    return StorageService.create(get_settings())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    storage = create_storage()
    app.state.storage = storage
    try:
        await run_in_threadpool(storage.initialize)
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
    responses=ERROR_RESPONSES,
)
app.include_router(upload.router)
app.include_router(download.router)
app.include_router(manage.router)


@app.exception_handler(InvalidBucketError)
async def invalid_bucket_handler(_request: Request, _exc: InvalidBucketError) -> JSONResponse:
    return error_response(
        status.HTTP_404_NOT_FOUND,
        "unknown_bucket",
        "Bucket was not found",
    )


@app.exception_handler(ObjectNotFoundError)
async def object_not_found_handler(_request: Request, _exc: ObjectNotFoundError) -> JSONResponse:
    return error_response(
        status.HTTP_404_NOT_FOUND,
        "file_not_found",
        "File was not found",
    )


@app.exception_handler(S3Error)
async def minio_error_handler(_request: Request, _exc: S3Error) -> JSONResponse:
    return error_response(
        status.HTTP_502_BAD_GATEWAY,
        "storage_error",
        "Object storage request failed",
    )


@app.exception_handler(HTTPException)
async def http_error_handler(_request: Request, exc: HTTPException) -> JSONResponse:
    code = STATUS_ERROR_CODES.get(exc.status_code, "request_error")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        message = "Authentication failed"
    return error_response(exc.status_code, code, message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    details = [
        {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return error_response(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "validation_error",
        "Request validation failed",
        details,
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    route = getattr(request.scope.get("route"), "path", "unknown")
    sanitized_error = RuntimeError(f"Unhandled {type(exc).__name__}")
    logger.error(
        "Unhandled request failure method=%s route=%s exception_type=%s",
        request.method,
        route,
        type(exc).__name__,
        exc_info=(RuntimeError, sanitized_error, exc.__traceback__),
    )
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "Internal server error",
    )


@app.get("/health", response_model=HealthResponse, tags=["health"])
async def health(request: Request) -> HealthResponse | JSONResponse:
    storage: StorageService = request.app.state.storage
    try:
        await run_in_threadpool(storage.check_health)
    except StorageUnavailableError:
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "storage_unavailable",
            "Object storage is unavailable",
        )
    except Exception as exc:
        logger.error("Unexpected health-check failure (%s)", type(exc).__name__)
        return error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "health_check_failed",
            "Health check failed",
        )
    return HealthResponse(status="ok", minio="ok")
