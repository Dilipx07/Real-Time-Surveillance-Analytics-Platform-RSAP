from uuid import UUID
import asyncio

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.dependencies import AdminUserDep, get_db
from app.middleware.audit import write_audit_log
from app.responses import PaginatedEnvelope, SuccessEnvelope, envelope
from app.schemas.user import PermissionCreate, UserCreate, UserUpdate
from app.security import hash_password
from app.services.rbac_service import (
    assert_can_manage_target,
    protect_last_super_admin,
    serialize_super_admin_mutation,
)
from app.services.session_service import enqueue_session_action, process_session_outbox_once

router = APIRouter()

USER_COLUMNS = "id, email, phone, role, is_active, is_deleted, whatsapp_number, created_by, created_at, updated_at"


@router.post("/", status_code=201, response_model=SuccessEnvelope)
async def create_user(payload: UserCreate, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    try:
        async with db.transaction():
            row = await db.fetchrow(
                f"""INSERT INTO auth.users(email, phone, password_hash, role, whatsapp_number, created_by)
                    VALUES(lower($1), $2, $3, $4, $5, $6) RETURNING {USER_COLUMNS}""",
                str(payload.email), payload.phone,
                await asyncio.to_thread(hash_password, payload.password), payload.role,
                payload.whatsapp_number, admin.id,
            )
            await write_audit_log(db, request, admin, "create", "auth.user", row["id"], {"role": payload.role})
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="A user with that email already exists") from exc
    return envelope(dict(row))


@router.get("/", response_model=PaginatedEnvelope)
async def list_users(
    admin: AdminUserDep,
    db: asyncpg.Connection = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    role: str | None = Query(default=None),
    is_active: bool | None = Query(default=None),
):
    conditions = ["is_deleted=false"]
    args: list = []
    if role:
        args.append(role); conditions.append(f"role=${len(args)}")
    if is_active is not None:
        args.append(is_active); conditions.append(f"is_active=${len(args)}")
    where = " AND ".join(conditions)
    total = await db.fetchval(f"SELECT count(*) FROM auth.users WHERE {where}", *args)
    args.extend([page_size, (page - 1) * page_size])
    rows = await db.fetch(
        f"SELECT {USER_COLUMNS} FROM auth.users WHERE {where} ORDER BY created_at DESC LIMIT ${len(args)-1} OFFSET ${len(args)}",
        *args,
    )
    return envelope({"items": [dict(row) for row in rows], "page": page, "page_size": page_size, "total": total})


@router.get("/{user_id}", response_model=SuccessEnvelope)
async def get_user(user_id: UUID, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    row = await db.fetchrow(f"SELECT {USER_COLUMNS} FROM auth.users WHERE id=$1 AND is_deleted=false", user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return envelope(dict(row))


@router.patch("/{user_id}", response_model=SuccessEnvelope)
async def update_user(payload: UserUpdate, user_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="At least one field is required")
    assignments, args = [], []
    for key, value in values.items():
        args.append(str(value) if key == "email" else value)
        expression = f"lower(${len(args)})" if key == "email" else f"${len(args)}"
        assignments.append(f"{key}={expression}")
    args.append(user_id)
    try:
        async with db.transaction():
            if "role" in values:
                await serialize_super_admin_mutation(db)
            target = await db.fetchrow(
                "SELECT role FROM auth.users WHERE id=$1 AND is_deleted=false FOR UPDATE", user_id
            )
            if target is None:
                raise HTTPException(status_code=404, detail="User not found")
            assert_can_manage_target(admin, target["role"])
            if values.get("role") == "admin" and admin.role != "super_admin":
                raise HTTPException(status_code=403, detail="Only a super administrator can assign the admin role")
            if "role" in values and target["role"] == "super_admin" and values["role"] != "super_admin":
                await protect_last_super_admin(db, user_id, target["role"])
            row = await db.fetchrow(
                f"UPDATE auth.users SET {', '.join(assignments)}, updated_at=NOW() WHERE id=${len(args)} AND is_deleted=false RETURNING {USER_COLUMNS}",
                *args,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="User not found")
            await write_audit_log(db, request, admin, "update", "auth.user", user_id, {"fields": list(values)})
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="A user with that email already exists") from exc
    return envelope(dict(row))


@router.delete("/{user_id}", response_model=SuccessEnvelope)
async def delete_user(user_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    async with db.transaction():
        await serialize_super_admin_mutation(db)
        target = await db.fetchrow(
            "SELECT role FROM auth.users WHERE id=$1 AND is_deleted=false FOR UPDATE", user_id
        )
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        assert_can_manage_target(admin, target["role"])
        await protect_last_super_admin(db, user_id, target["role"])
        changed = await db.fetchval(
            "UPDATE auth.users SET is_deleted=true, is_active=false, updated_at=NOW() WHERE id=$1 AND is_deleted=false RETURNING id",
            user_id,
        )
        if changed is None:
            raise HTTPException(status_code=404, detail="User not found")
        await db.execute("UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND revoked_at IS NULL", user_id)
        await enqueue_session_action(db, user_id, "revoke")
        await write_audit_log(db, request, admin, "delete", "auth.user", user_id)
    await process_session_outbox_once(request.app)
    return envelope({"deleted": True})


@router.patch("/{user_id}/toggle-active", response_model=SuccessEnvelope)
async def toggle_active(user_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    async with db.transaction():
        await serialize_super_admin_mutation(db)
        target = await db.fetchrow(
            "SELECT role, is_active FROM auth.users WHERE id=$1 AND is_deleted=false FOR UPDATE",
            user_id,
        )
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        assert_can_manage_target(admin, target["role"])
        if target["is_active"]:
            await protect_last_super_admin(db, user_id, target["role"])
        row = await db.fetchrow(
            f"UPDATE auth.users SET is_active=NOT is_active, updated_at=NOW() WHERE id=$1 AND is_deleted=false RETURNING {USER_COLUMNS}",
            user_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        if not row["is_active"]:
            await db.execute("UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND revoked_at IS NULL", user_id)
            await enqueue_session_action(db, user_id, "revoke")
        await write_audit_log(db, request, admin, "toggle_active", "auth.user", user_id, {"is_active": row["is_active"]})
    if not row["is_active"]:
        await process_session_outbox_once(request.app)
    return envelope(dict(row))


@router.get("/{user_id}/permissions", response_model=SuccessEnvelope)
async def list_permissions(user_id: UUID, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    if not await db.fetchval("SELECT EXISTS(SELECT 1 FROM auth.users WHERE id=$1 AND is_deleted=false)", user_id):
        raise HTTPException(status_code=404, detail="User not found")
    rows = await db.fetch(
        "SELECT id, user_id, resource, actions, constraints, granted_by, created_at FROM rbac.permissions WHERE user_id=$1 ORDER BY created_at",
        user_id,
    )
    return envelope([dict(row) for row in rows])


@router.post("/{user_id}/permissions", status_code=201, response_model=SuccessEnvelope)
async def grant_permission(payload: PermissionCreate, user_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    if any(not action.strip() for action in payload.actions):
        raise HTTPException(status_code=422, detail="Permission actions cannot be empty")
    async with db.transaction():
        target = await db.fetchrow(
            "SELECT role FROM auth.users WHERE id=$1 AND is_deleted=false FOR UPDATE", user_id
        )
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        assert_can_manage_target(admin, target["role"])
        row = await db.fetchrow(
            """INSERT INTO rbac.permissions(user_id, resource, actions, constraints, granted_by)
               VALUES($1, $2, $3, $4::jsonb, $5)
               RETURNING id, user_id, resource, actions, constraints, granted_by, created_at""",
            user_id, payload.resource, payload.actions, payload.constraints, admin.id,
        )
        await write_audit_log(db, request, admin, "grant", "rbac.permission", row["id"], {"user_id": str(user_id)})
    return envelope(dict(row))


@router.delete("/{user_id}/permissions/{permission_id}", response_model=SuccessEnvelope)
async def revoke_permission(user_id: UUID, permission_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    async with db.transaction():
        target = await db.fetchrow(
            "SELECT role FROM auth.users WHERE id=$1 AND is_deleted=false FOR UPDATE", user_id
        )
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        assert_can_manage_target(admin, target["role"])
        removed = await db.fetchval("DELETE FROM rbac.permissions WHERE id=$1 AND user_id=$2 RETURNING id", permission_id, user_id)
        if removed is None:
            raise HTTPException(status_code=404, detail="Permission not found")
        await write_audit_log(db, request, admin, "revoke", "rbac.permission", permission_id, {"user_id": str(user_id)})
    return envelope({"deleted": True})
