from typing import Any

from fastapi.responses import JSONResponse


def envelope(data: Any = None) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None}


def error_response(message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "data": None, "error": message},
    )
