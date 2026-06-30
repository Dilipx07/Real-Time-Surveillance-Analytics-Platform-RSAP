from typing import Any, Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel


class SuccessEnvelope(BaseModel):
    success: Literal[True] = True
    data: Any
    error: None = None


class PaginatedData(BaseModel):
    items: list[Any]
    page: int
    page_size: int
    total: int


class PaginatedEnvelope(SuccessEnvelope):
    data: PaginatedData


class AuthenticationData(BaseModel):
    access_token: str
    refresh_token: str
    session_token: str
    token_type: Literal["bearer"]
    expires_in: int
    user: dict[str, Any]


class AuthenticationEnvelope(SuccessEnvelope):
    data: AuthenticationData


class RefreshData(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"]
    expires_in: int


class RefreshEnvelope(SuccessEnvelope):
    data: RefreshData


class ErrorEnvelope(BaseModel):
    success: Literal[False] = False
    data: None = None
    error: str


STANDARD_ERROR_RESPONSES = {
    400: {"model": ErrorEnvelope},
    401: {"model": ErrorEnvelope},
    403: {"model": ErrorEnvelope},
    404: {"model": ErrorEnvelope},
    409: {"model": ErrorEnvelope},
    422: {"model": ErrorEnvelope},
    500: {"model": ErrorEnvelope},
    503: {"model": ErrorEnvelope},
}


def envelope(data: Any = None) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "data": None, "error": message},
    )
