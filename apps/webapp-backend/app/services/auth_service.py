from datetime import timedelta
from typing import Any

from app.config import get_settings
from app.security import create_token


def issue_token_pair(user_id: str) -> tuple[str, str]:
    settings = get_settings()
    access = create_token(user_id, "access", timedelta(minutes=settings.jwt_access_expire_minutes))
    refresh = create_token(user_id, "refresh", timedelta(days=settings.jwt_refresh_expire_days))
    return access, refresh


def public_user(row: Any, permissions: list[dict]) -> dict:
    return {"id": row["id"], "email": row["email"], "role": row["role"], "permissions": permissions}
