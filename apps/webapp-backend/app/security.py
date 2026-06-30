from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import bcrypt
from jose import JWTError, jwt

from app.config import get_settings


def utc_now() -> datetime:
    return datetime.now(UTC)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def session_identifier(session_token: str) -> str:
    return __import__("hashlib").sha256(session_token.encode("utf-8")).hexdigest()


def token_hash(token: str) -> str:
    return __import__("hashlib").sha256(token.encode("utf-8")).hexdigest()


def create_token(
    subject: str,
    token_type: str,
    expires_delta: timedelta,
    session_id: str,
    token_id: str | None = None,
) -> tuple[str, str]:
    now = utc_now()
    jti = token_id or str(uuid4())
    payload = {
        "sub": subject,
        "type": token_type,
        "sid": session_id,
        "jti": jti,
        "iat": now,
        "exp": now + expires_delta,
    }
    settings = get_settings()
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), jti


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if (
        payload.get("type") != expected_type
        or not payload.get("sub")
        or not payload.get("sid")
        or not payload.get("jti")
    ):
        raise JWTError("Invalid token type, subject, session, or identifier")
    return payload
