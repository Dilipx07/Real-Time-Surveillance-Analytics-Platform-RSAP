from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.dependencies import AdminUserDep, CurrentUserDep, get_db
from app.middleware.audit import write_audit_log
from app.responses import PaginatedEnvelope, SuccessEnvelope, envelope
from app.schemas.license import LicenseCreate, LicenseUpdate
from app.services.license_service import generate_license_key
from app.services.rbac_service import (
    assert_can_manage_target,
    protect_last_super_admin,
    serialize_super_admin_mutation,
)
from app.services.session_service import enqueue_session_action, process_session_outbox_once

router = APIRouter()
LICENSE_COLUMNS = "id, user_id, license_key, features, max_cameras, analytics_modules, valid_from, valid_until, is_active, created_by, created_at, updated_at"


@router.post("/", status_code=201, response_model=SuccessEnvelope)
async def create_license(payload: LicenseCreate, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    key = generate_license_key(payload.user_id, payload.valid_until)
    async with db.transaction():
        target = await db.fetchrow(
            "SELECT role FROM auth.users WHERE id=$1 AND is_deleted=false FOR UPDATE",
            payload.user_id,
        )
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        assert_can_manage_target(admin, target["role"])
        row = await db.fetchrow(
            f"""INSERT INTO rbac.licenses(user_id, license_key, features, max_cameras, analytics_modules, valid_from, valid_until, created_by)
                VALUES($1, $2, $3::jsonb, $4, $5::jsonb, $6, $7, $8) RETURNING {LICENSE_COLUMNS}""",
            payload.user_id, key, payload.features, payload.max_cameras,
            payload.analytics_modules, payload.valid_from, payload.valid_until, admin.id,
        )
        await enqueue_session_action(db, payload.user_id, "reconcile")
        await write_audit_log(db, request, admin, "create", "rbac.license", row["id"], {"user_id": str(payload.user_id)})
    await process_session_outbox_once(request.app)
    return envelope(dict(row))


@router.get("/", response_model=PaginatedEnvelope)
async def list_licenses(admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    total = await db.fetchval("SELECT count(*) FROM rbac.licenses")
    rows = await db.fetch(
        f"SELECT {LICENSE_COLUMNS} FROM rbac.licenses ORDER BY created_at DESC LIMIT $1 OFFSET $2",
        page_size, (page - 1) * page_size,
    )
    return envelope({"items": [dict(row) for row in rows], "page": page, "page_size": page_size, "total": total})


@router.get("/user/{user_id}", response_model=SuccessEnvelope)
async def get_user_license(user_id: UUID, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    row = await db.fetchrow(
        f"SELECT {LICENSE_COLUMNS} FROM rbac.licenses WHERE user_id=$1 ORDER BY valid_until DESC LIMIT 1", user_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="License not found")
    return envelope(dict(row))


@router.get("/{license_id}", response_model=SuccessEnvelope)
async def get_license(license_id: UUID, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    row = await db.fetchrow(f"SELECT {LICENSE_COLUMNS} FROM rbac.licenses WHERE id=$1", license_id)
    if row is None:
        raise HTTPException(status_code=404, detail="License not found")
    return envelope(dict(row))


@router.patch("/{license_id}", response_model=SuccessEnvelope)
async def update_license(payload: LicenseUpdate, license_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="At least one field is required")
    assignments, args = [], []
    for key, value in values.items():
        args.append(value)
        cast = "::jsonb" if key in {"features", "analytics_modules"} else ""
        assignments.append(f"{key}=${len(args)}{cast}")
    args.append(license_id)
    async with db.transaction():
        await serialize_super_admin_mutation(db)
        current = await db.fetchrow(
            """SELECT l.user_id, l.valid_from, l.valid_until, l.is_active, u.role
               FROM rbac.licenses l JOIN auth.users u ON u.id=l.user_id
               WHERE l.id=$1 FOR UPDATE OF l, u""",
            license_id,
        )
        if current is None:
            raise HTTPException(status_code=404, detail="License not found")
        assert_can_manage_target(admin, current["role"])
        valid_from = values.get("valid_from", current["valid_from"])
        valid_until = values.get("valid_until", current["valid_until"])
        if valid_until <= valid_from:
            raise HTTPException(status_code=422, detail="valid_until must be after valid_from")
        remains_active = await db.fetchval(
            "SELECT $1::boolean AND $2::timestamptz <= NOW() AND $3::timestamptz > NOW()",
            values.get("is_active", current["is_active"]),
            valid_from,
            valid_until,
        )
        if not remains_active:
            await protect_last_super_admin(
                db,
                current["user_id"],
                current["role"],
                excluded_license_id=license_id,
            )
        row = await db.fetchrow(
            f"UPDATE rbac.licenses SET {', '.join(assignments)}, updated_at=NOW() WHERE id=${len(args)} RETURNING {LICENSE_COLUMNS}",
            *args,
        )
        await enqueue_session_action(db, row["user_id"], "reconcile")
        await write_audit_log(db, request, admin, "update", "rbac.license", license_id, {"fields": list(values)})
    await process_session_outbox_once(request.app)
    return envelope(dict(row))


@router.delete("/{license_id}/expire", response_model=SuccessEnvelope)
async def expire_license(license_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    async with db.transaction():
        await serialize_super_admin_mutation(db)
        target = await db.fetchrow(
            """SELECT u.id, u.role FROM rbac.licenses l JOIN auth.users u ON u.id=l.user_id
               WHERE l.id=$1 FOR UPDATE OF l, u""",
            license_id,
        )
        if target is None:
            raise HTTPException(status_code=404, detail="License not found")
        assert_can_manage_target(admin, target["role"])
        await protect_last_super_admin(
            db, target["id"], target["role"], excluded_license_id=license_id
        )
        row = await db.fetchrow(
            f"UPDATE rbac.licenses SET valid_until=NOW(), is_active=false, updated_at=NOW() WHERE id=$1 RETURNING {LICENSE_COLUMNS}",
            license_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="License not found")
        await db.execute("UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND revoked_at IS NULL", row["user_id"])
        await enqueue_session_action(db, row["user_id"], "revoke")
        await write_audit_log(db, request, admin, "expire", "rbac.license", license_id)
    await process_session_outbox_once(request.app)
    return envelope(dict(row))


@router.get("/{license_id}/verify", response_model=SuccessEnvelope)
async def verify_license(
    license_id: UUID,
    user: CurrentUserDep,
    db: asyncpg.Connection = Depends(get_db),
):
    row = await db.fetchrow(
        """SELECT user_id, valid_until, features, max_cameras, analytics_modules,
                  (is_active AND valid_from <= NOW() AND valid_until > NOW()) AS valid
           FROM rbac.licenses WHERE id=$1""",
        license_id,
    )
    if row is None or row["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="License not found")
    return envelope({
        "valid": row["valid"], "expires_at": row["valid_until"], "features": row["features"],
        "max_cameras": row["max_cameras"], "analytics_modules": row["analytics_modules"],
    })
