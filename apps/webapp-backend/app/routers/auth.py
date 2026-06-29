import hmac
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError

from app.config import get_settings
from app.dependencies import CurrentUserDep, bearer, get_db
from app.middleware.audit import write_audit_log
from app.responses import envelope
from app.schemas.auth import LoginRequest, RefreshRequest
from app.security import decode_token, verify_password
from app.services.auth_service import issue_token_pair, public_user

router = APIRouter()


@router.post("/login")
async def login(payload: LoginRequest, request: Request, db: asyncpg.Connection = Depends(get_db)):
    user = await db.fetchrow(
        """SELECT id, email, password_hash, role FROM auth.users
           WHERE lower(email)=lower($1) AND is_active=true AND is_deleted=false""",
        str(payload.email),
    )
    if user is None or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    license_row = await db.fetchrow(
        """SELECT valid_until FROM rbac.licenses
           WHERE user_id=$1 AND is_active=true AND valid_from <= NOW() AND valid_until > NOW()
           ORDER BY valid_until DESC LIMIT 1""",
        user["id"],
    )
    if license_row is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active license")
    now = datetime.now(UTC)
    ttl = int((license_row["valid_until"] - now).total_seconds())
    if ttl <= 0:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="License has expired")

    redis = request.app.state.redis
    key = f"session:{user['id']}"
    previous = await redis.get(key)
    session_token = str(uuid4())
    access_token, refresh_token = issue_token_pair(str(user["id"]))
    refresh_ttl = min(ttl, get_settings().jwt_refresh_expire_days * 86400)
    async with db.transaction():
        if previous:
            await db.execute(
                "UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND session_token=$2 AND revoked_at IS NULL",
                user["id"], previous,
            )
        await db.execute(
            """INSERT INTO auth.sessions(user_id, session_token, device_fingerprint, ip_address, expires_at)
               VALUES($1, $2, $3, $4, $5)""",
            user["id"], session_token, payload.device_fingerprint,
            request.client.host if request.client else None, license_row["valid_until"],
        )
        await write_audit_log(db, request, user["id"], "login", "auth.session", metadata={"user_id": str(user["id"])})
    await redis.set(key, session_token, ex=ttl)
    await redis.set(f"refresh:{user['id']}", refresh_token, ex=refresh_ttl)
    permissions = [dict(row) for row in await db.fetch(
        "SELECT id, resource, actions, constraints FROM rbac.permissions WHERE user_id=$1 ORDER BY created_at",
        user["id"],
    )]
    return envelope({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "session_token": session_token,
        "token_type": "bearer",
        "expires_in": get_settings().jwt_access_expire_minutes * 60,
        "user": public_user(user, permissions),
    })


@router.post("/logout")
async def logout(user: CurrentUserDep, request: Request, db: asyncpg.Connection = Depends(get_db)):
    session_token = request.state.session_token
    async with db.transaction():
        await db.execute(
            "UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND session_token=$2 AND revoked_at IS NULL",
            user.id, session_token,
        )
        await write_audit_log(db, request, user, "logout", "auth.session")
    await request.app.state.redis.delete(f"session:{user.id}", f"refresh:{user.id}")
    return envelope({"logged_out": True})


@router.post("/refresh")
async def refresh(
    payload: RefreshRequest,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session_token: str | None = Header(default=None, alias="X-Session-Token"),
    db: asyncpg.Connection = Depends(get_db),
):
    if credentials is None or not session_token or not hmac.compare_digest(credentials.credentials, payload.refresh_token):
        raise HTTPException(status_code=401, detail="Refresh and session tokens are required")
    try:
        token_payload = decode_token(payload.refresh_token, expected_type="refresh")
        user_id = UUID(token_payload["sub"])
    except (JWTError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token") from exc
    redis = request.app.state.redis
    stored_refresh, stored_session = await redis.mget(f"refresh:{user_id}", f"session:{user_id}")
    if not stored_refresh or not stored_session or not hmac.compare_digest(stored_refresh, payload.refresh_token) or not hmac.compare_digest(stored_session, session_token):
        raise HTTPException(status_code=401, detail="Refresh session is invalid or expired")
    access, _ = issue_token_pair(str(user_id))
    await write_audit_log(db, request, user_id, "refresh", "auth.session")
    return envelope({"access_token": access, "token_type": "bearer", "expires_in": get_settings().jwt_access_expire_minutes * 60})


@router.get("/me")
async def current_user(user: CurrentUserDep):
    return envelope({"id": user.id, "email": user.email, "role": user.role, "permissions": user.permissions})
