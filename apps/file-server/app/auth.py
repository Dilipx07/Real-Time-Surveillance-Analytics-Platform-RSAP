import hmac

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from app.config import Settings, get_settings


service_token_header = APIKeyHeader(name="X-Service-Token", auto_error=False)


def verify_service_token(
    supplied_token: str | None = Depends(service_token_header),
    settings: Settings = Depends(get_settings),
) -> None:
    if supplied_token is None or not hmac.compare_digest(
        supplied_token.encode("utf-8"),
        settings.file_server_service_token.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing service token",
        )
