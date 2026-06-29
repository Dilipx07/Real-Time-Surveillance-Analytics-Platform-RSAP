import hmac
from datetime import UTC, datetime
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from app.dependencies import AdminUserDep, get_db
from app.middleware.audit import write_audit_log
from app.responses import envelope
from app.schemas.license import LicenseCreate, LicenseUpdate
from app.services.license_service import generate_license_key

router = APIRouter()
LICENSE_COLUMNS = "id, user_id, license_key, features, max_cameras, analytics_modules, valid_from, valid_until, is_active, created_by, created_at, updated_at"


@router.post("/", status_code=201)
async def create_license(payload: LicenseCreate, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    if not await db.fetchval("SELECT EXISTS(SELECT 1 FROM auth.users WHERE id=$1 AND is_deleted=false)", payload.user_id):
        raise HTTPException(status_code=404, detail="User not found")
    key = generate_license_key(payload.user_id, payload.valid_until)
    async with db.transaction():
        row = await db.fetchrow(
            f"""INSERT INTO rbac.licenses(user_id, license_key, features, max_cameras, analytics_modules, valid_from, valid_until, created_by)
                VALUES($1, $2, $3::jsonb, $4, $5::jsonb, $6, $7, $8) RETURNING {LICENSE_COLUMNS}""",
            payload.user_id, key, payload.features, payload.max_cameras,
            payload.analytics_modules, payload.valid_from, payload.valid_until, admin.id,
        )
        await write_audit_log(db, request, admin, "create", "rbac.license", row["id"], {"user_id": str(payload.user_id)})
    ttl = int((payload.valid_until - datetime.now(UTC)).total_seconds())
    if ttl > 0 and await request.app.state.redis.exists(f"session:{payload.user_id}"):
        await request.app.state.redis.expire(f"session:{payload.user_id}", ttl)
    return envelope(dict(row))


@router.get("/")
async def list_licenses(admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    total = await db.fetchval("SELECT count(*) FROM rbac.licenses")
    rows = await db.fetch(
        f"SELECT {LICENSE_COLUMNS} FROM rbac.licenses ORDER BY created_at DESC LIMIT $1 OFFSET $2",
        page_size, (page - 1) * page_size,
    )
    return envelope({"items": [dict(row) for row in rows], "page": page, "page_size": page_size, "total": total})


@router.get("/user/{user_id}")
async def get_user_license(user_id: UUID, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    row = await db.fetchrow(
        f"SELECT {LICENSE_COLUMNS} FROM rbac.licenses WHERE user_id=$1 ORDER BY valid_until DESC LIMIT 1", user_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="License not found")
    return envelope(dict(row))


@router.get("/{license_id}")
async def get_license(license_id: UUID, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    row = await db.fetchrow(f"SELECT {LICENSE_COLUMNS} FROM rbac.licenses WHERE id=$1", license_id)
    if row is None:
        raise HTTPException(status_code=404, detail="License not found")
    return envelope(dict(row))


@router.patch("/{license_id}")
async def update_license(payload: LicenseUpdate, license_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="At least one field is required")
    current = await db.fetchrow("SELECT user_id, valid_from, valid_until FROM rbac.licenses WHERE id=$1", license_id)
    if current is None:
        raise HTTPException(status_code=404, detail="License not found")
    valid_from = values.get("valid_from", current["valid_from"])
    valid_until = values.get("valid_until", current["valid_until"])
    if valid_until <= valid_from:
        raise HTTPException(status_code=422, detail="valid_until must be after valid_from")
    assignments, args = [], []
    for key, value in values.items():
        args.append(value)
        cast = "::jsonb" if key in {"features", "analytics_modules"} else ""
        assignments.append(f"{key}=${len(args)}{cast}")
    args.append(license_id)
    async with db.transaction():
        row = await db.fetchrow(
            f"UPDATE rbac.licenses SET {', '.join(assignments)}, updated_at=NOW() WHERE id=${len(args)} RETURNING {LICENSE_COLUMNS}",
            *args,
        )
        await write_audit_log(db, request, admin, "update", "rbac.license", license_id, {"fields": list(values)})
    redis = request.app.state.redis
    ttl = int((row["valid_until"] - datetime.now(UTC)).total_seconds())
    if not row["is_active"] or ttl <= 0:
        await redis.delete(f"session:{row['user_id']}", f"refresh:{row['user_id']}")
        await db.execute("UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND revoked_at IS NULL", row["user_id"])
    elif await redis.exists(f"session:{row['user_id']}"):
        await redis.expire(f"session:{row['user_id']}", ttl)
    return envelope(dict(row))


@router.delete("/{license_id}/expire")
async def expire_license(license_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    async with db.transaction():
        row = await db.fetchrow(
            f"UPDATE rbac.licenses SET valid_until=NOW(), is_active=false, updated_at=NOW() WHERE id=$1 RETURNING {LICENSE_COLUMNS}",
            license_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="License not found")
        await db.execute("UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND revoked_at IS NULL", row["user_id"])
        await write_audit_log(db, request, admin, "expire", "rbac.license", license_id)
    await request.app.state.redis.delete(f"session:{row['user_id']}", f"refresh:{row['user_id']}")
    return envelope(dict(row))


@router.get("/{license_id}/verify")
async def verify_license(
    license_id: UUID,
    request: Request,
    session_token: str | None = Header(default=None, alias="X-Session-Token"),
    db: asyncpg.Connection = Depends(get_db),
):
    if not session_token:
        raise HTTPException(status_code=401, detail="Session token is required")
    session = await db.fetchrow(
        "SELECT user_id FROM auth.sessions WHERE session_token=$1 AND revoked_at IS NULL AND expires_at > NOW()",
        session_token,
    )
    if session is None:
        raise HTTPException(status_code=401, detail="Session is invalid or expired")
    live = await request.app.state.redis.get(f"session:{session['user_id']}")
    if live is None or not hmac.compare_digest(live, session_token):
        raise HTTPException(status_code=401, detail="Session is invalid or expired")
    row = await db.fetchrow(
        "SELECT user_id, valid_until, features, max_cameras, analytics_modules, is_active, valid_from FROM rbac.licenses WHERE id=$1",
        license_id,
    )
    if row is None or row["user_id"] != session["user_id"]:
        raise HTTPException(status_code=404, detail="License not found")
    now = datetime.now(UTC)
    valid = row["is_active"] and row["valid_from"] <= now < row["valid_until"]
    return envelope({
        "valid": valid, "expires_at": row["valid_until"], "features": row["features"],
        "max_cameras": row["max_cameras"], "analytics_modules": row["analytics_modules"],
    })
