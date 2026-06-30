from datetime import timedelta
from typing import Any

from app.config import get_settings
from app.security import create_token


def issue_token_pair(user_id: str, session_id: str) -> tuple[str, str, str]:
    settings = get_settings()
    access, _ = create_token(
        user_id, "access", timedelta(minutes=settings.jwt_access_expire_minutes), session_id
    )
    refresh, refresh_jti = create_token(
        user_id, "refresh", timedelta(days=settings.jwt_refresh_expire_days), session_id
    )
    return access, refresh, refresh_jti


def public_user(row: Any, permissions: list[dict]) -> dict:
    return {"id": row["id"], "email": row["email"], "role": row["role"], "permissions": permissions}
