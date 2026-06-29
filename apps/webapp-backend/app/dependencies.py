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

bearer = HTTPBearer(auto_error=False)


async def get_db(request: Request) -> AsyncIterator[asyncpg.Connection]:
    async for connection in get_connection(request):
        yield connection


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


async def verify_dual_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session_token: Annotated[str | None, Header(alias="X-Session-Token")] = None,
    db: asyncpg.Connection = Depends(get_db),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer" or not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Both bearer and session tokens are required")
    try:
        payload = decode_token(credentials.credentials)
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired access token") from exc

    try:
        user_id = UUID(payload["sub"])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject") from exc
    stored_token = await request.app.state.redis.get(f"session:{user_id}")
    if stored_token is None or not __import__("hmac").compare_digest(stored_token, session_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session is invalid or expired")

    user = await db.fetchrow(
        "SELECT id, email, role FROM auth.users WHERE id=$1 AND is_active=true AND is_deleted=false",
        user_id,
    )
    if user is None:
        await request.app.state.redis.delete(f"session:{user_id}", f"refresh:{user_id}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive or unavailable")
    permissions = await db.fetch(
        "SELECT id, resource, actions, constraints FROM rbac.permissions WHERE user_id=$1 ORDER BY created_at",
        user["id"],
    )
    current = CurrentUser(
        id=user["id"], email=user["email"], role=user["role"], permissions=[dict(row) for row in permissions]
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
