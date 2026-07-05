"""Consistent local API response envelopes and error handling."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.authorization import AuthorizationError
from app.clients import ExternalServiceError
from app.dtos import PermanentContractError
from app.services import ConflictError

logger = logging.getLogger("rsap.desktop")


def envelope(data: Any = None) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def error(message: str, status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "data": None, "error": {"code": code, "message": message}},
    )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return error(message, exc.status_code, f"http_{exc.status_code}")

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = exc.errors()
        location = ".".join(str(part) for part in details[0].get("loc", ())) if details else "request"
        return error(f"Invalid {location}", 422, "validation_error")

    @app.exception_handler(ExternalServiceError)
    async def external_error(_: Request, exc: ExternalServiceError) -> JSONResponse:
        return error(exc.message, exc.status_code, exc.code)

    @app.exception_handler(AuthorizationError)
    async def authorization_error(_: Request, exc: AuthorizationError) -> JSONResponse:
        return error(str(exc), 403, "forbidden")

    @app.exception_handler(ConflictError)
    async def conflict_error(_: Request, exc: ConflictError) -> JSONResponse:
        return error(str(exc), 409, "conflict")

    @app.exception_handler(PermanentContractError)
    async def contract_error(_: Request, exc: PermanentContractError) -> JSONResponse:
        return error(str(exc), 422, "contract_error")

    @app.exception_handler(Exception)
    async def internal_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled desktop API error", exc_info=exc)
        return error("Internal server error", 500, "internal_error")
