from typing import Any

from fastapi.responses import JSONResponse

from app.schemas import ErrorDetail, ErrorResponse


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(code=code, message=message, details=details)
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


STATUS_ERROR_CODES = {
    400: "invalid_request",
    401: "invalid_service_token",
    403: "forbidden",
    404: "not_found",
    413: "file_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    500: "internal_error",
    502: "storage_error",
    503: "storage_unavailable",
}
