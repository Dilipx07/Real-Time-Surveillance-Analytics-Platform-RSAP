from collections.abc import AsyncIterator, Callable
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from redis.asyncio import Redis

from app.database import get_connection
from app.models.user import CurrentUser
from app.security import decode_token
from app.services.session_service import validate_session_state

bearer = HTTPBearer(auto_error=False)


async def get_db(request: Request) -> AsyncIterator[asyncpg.Connection]:
    async for connection in get_connection(request):
        yield connection


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


async def verify_dual_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session_token: Annotated[str, Header(alias="X-Session-Token")],
    db: asyncpg.Connection = Depends(get_db),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Both bearer and session tokens are required")
    try:
        payload = decode_token(credentials.credentials)
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token") from exc

    try:
        user_id = UUID(payload["sub"])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject") from exc
    validated = await validate_session_state(
        request.app.state.redis, db, user_id, session_token, payload["sid"]
    )
    if validated is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or unavailable")
    state, user, permissions = validated
    current = CurrentUser(
        id=user["id"],
        email=user["email"],
        role=user["role"],
        session_id=state["sid"],
        license_id=UUID(state["license_id"]),
        permissions=permissions,
    )
    request.state.current_user = current
    request.state.session_token = session_token
    return current


def require_roles(*roles: str) -> Callable:
    async def checker(user: CurrentUser = Depends(verify_dual_token)) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return user

    return checker


CurrentUserDep = Annotated[CurrentUser, Depends(verify_dual_token)]
AdminUserDep = Annotated[CurrentUser, Depends(require_roles("admin", "super_admin"))]
