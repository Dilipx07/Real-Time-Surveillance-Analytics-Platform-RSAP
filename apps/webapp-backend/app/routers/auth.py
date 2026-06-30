import hmac
import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials
from jose import JWTError

from app.config import get_settings
from app.dependencies import CurrentUserDep, bearer, get_db
from app.middleware.audit import request_ip, write_audit_log
from app.responses import AuthenticationEnvelope, RefreshEnvelope, SuccessEnvelope, envelope
from app.schemas.auth import LoginRequest, RefreshRequest
from app.security import decode_token, session_identifier, token_hash, verify_password
from app.services.auth_service import issue_token_pair, public_user
from app.services.session_service import (
    activate_session,
    delete_if_sid,
    enqueue_session_action,
    load_json,
    process_session_outbox_once,
    refresh_state,
    rotate_refresh,
    session_state,
    validate_session_state,
)

router = APIRouter()


@router.post("/login", response_model=AuthenticationEnvelope)
async def login(payload: LoginRequest, request: Request, db: asyncpg.Connection = Depends(get_db)):
    user = await db.fetchrow(
        """SELECT id, email, password_hash, role FROM auth.users
           WHERE lower(email)=lower($1) AND is_active=true AND is_deleted=false""",
        str(payload.email),
    )
    if user is None or not await asyncio.to_thread(
        verify_password, payload.password, user["password_hash"]
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    session_token = str(uuid4())
    sid = session_identifier(session_token)
    access_token, refresh_token, refresh_jti = issue_token_pair(str(user["id"]), sid)
    refresh_expires_at = int(decode_token(refresh_token, expected_type="refresh")["exp"])
    activated = False
    try:
        async with db.transaction():
            locked_user = await db.fetchrow(
                """SELECT id, email, password_hash, role FROM auth.users
                   WHERE id=$1 AND is_active=true AND is_deleted=false FOR UPDATE""",
                user["id"],
            )
            if locked_user is None:
                raise HTTPException(status_code=401, detail="User is inactive or unavailable")
            license_row = await db.fetchrow(
                """SELECT id, valid_from, valid_until FROM rbac.licenses
                   WHERE user_id=$1 AND is_active=true AND valid_from <= NOW()
                     AND valid_until > NOW() ORDER BY valid_until DESC LIMIT 1""",
                user["id"],
            )
            if license_row is None:
                raise HTTPException(status_code=403, detail="No active license")
            ttl = int((license_row["valid_until"] - datetime.now(UTC)).total_seconds())
            if ttl <= 0:
                raise HTTPException(status_code=403, detail="License has expired")
            await db.execute(
                "UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND revoked_at IS NULL",
                user["id"],
            )
            await db.execute(
                """INSERT INTO auth.sessions(user_id, session_token, device_fingerprint, ip_address, expires_at)
                   VALUES($1, $2, $3, $4, $5)""",
                user["id"], session_token, payload.device_fingerprint,
                request_ip(request), license_row["valid_until"],
            )
            await write_audit_log(
                db, request, user["id"], "login", "auth.session",
                metadata={"user_id": str(user["id"]), "sid": sid},
            )
            await activate_session(
                request.app.state.redis,
                user["id"],
                session_state(
                    session_token,
                    user["id"],
                    license_row["id"],
                    license_row["valid_from"],
                    license_row["valid_until"],
                    refresh_expires_at,
                    refresh_expires_at,
                ),
                refresh_state(refresh_token, refresh_jti, sid, refresh_expires_at),
                min(refresh_expires_at, int(license_row["valid_until"].timestamp())),
                min(refresh_expires_at, int(license_row["valid_until"].timestamp())),
            )
            activated = True
    except Exception:
        if activated:
            try:
                await delete_if_sid(request.app.state.redis, user["id"], sid)
            except Exception:
                request.app.state.logger.exception("Failed to compensate Redis login activation")
        raise
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


@router.post("/logout", response_model=SuccessEnvelope)
async def logout(user: CurrentUserDep, request: Request, db: asyncpg.Connection = Depends(get_db)):
    session_token = request.state.session_token
    async with db.transaction():
        await db.execute(
            "UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND session_token=$2 AND revoked_at IS NULL",
            user.id, session_token,
        )
        await enqueue_session_action(db, user.id, "revoke", {"sid": user.session_id})
        await write_audit_log(db, request, user, "logout", "auth.session")
    await process_session_outbox_once(request.app)
    return envelope({"logged_out": True, "revocation_queued": True})


@router.post("/refresh", response_model=RefreshEnvelope)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session_token: str = Header(alias="X-Session-Token"),
    db: asyncpg.Connection = Depends(get_db),
):
    if credentials is None or not hmac.compare_digest(credentials.credentials, payload.refresh_token):
        raise HTTPException(status_code=401, detail="Refresh and session tokens are required")
    try:
        token_payload = decode_token(payload.refresh_token, expected_type="refresh")
        user_id = UUID(token_payload["sub"])
    except (JWTError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token") from exc
    sid = str(token_payload["sid"])
    validated = await validate_session_state(
        request.app.state.redis, db, user_id, session_token, sid
    )
    if validated is None:
        raise HTTPException(status_code=401, detail="Refresh session is invalid or expired")
    session_ttl = await request.app.state.redis.ttl(f"session:{user_id}")
    if session_ttl <= 0:
        raise HTTPException(status_code=401, detail="Refresh session is invalid or expired")
    access, replacement_refresh, replacement_jti = issue_token_pair(str(user_id), sid)
    replacement_expires_at = int(
        decode_token(replacement_refresh, expected_type="refresh")["exp"]
    )
    rotated = await rotate_refresh(
        request.app.state.redis,
        user_id,
        payload.refresh_token,
        str(token_payload["jti"]),
        sid,
        refresh_state(replacement_refresh, replacement_jti, sid, replacement_expires_at),
    )
    if not rotated:
        current = await load_json(request.app.state.redis, f"refresh:{user_id}")
        if current is None or hmac.compare_digest(str(current.get("sid", "")), sid):
            async with db.transaction():
                await db.execute(
                    "UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND session_token=$2 AND revoked_at IS NULL",
                    user_id,
                    session_token,
                )
                await enqueue_session_action(db, user_id, "revoke", {"sid": sid})
                await write_audit_log(
                    db, request, user_id, "refresh_replay", "auth.session", metadata={"sid": sid}
                )
            await process_session_outbox_once(request.app)
        raise HTTPException(status_code=401, detail="Refresh token was already consumed")
    await write_audit_log(db, request, user_id, "refresh", "auth.session")
    return envelope({
        "access_token": access,
        "refresh_token": replacement_refresh,
        "token_type": "bearer",
        "expires_in": get_settings().jwt_access_expire_minutes * 60,
    })


@router.get("/me", response_model=SuccessEnvelope)
async def current_user(user: CurrentUserDep):
    return envelope({"id": user.id, "email": user.email, "role": user.role, "permissions": user.permissions})
