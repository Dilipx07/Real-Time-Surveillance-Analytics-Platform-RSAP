from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.dependencies import AdminUserDep, get_db
from app.middleware.audit import write_audit_log
from app.responses import envelope
from app.schemas.user import PermissionCreate, UserCreate, UserUpdate
from app.security import hash_password

router = APIRouter()

USER_COLUMNS = "id, email, phone, role, is_active, is_deleted, whatsapp_number, created_by, created_at, updated_at"


@router.post("/", status_code=201)
async def create_user(payload: UserCreate, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    try:
        async with db.transaction():
            row = await db.fetchrow(
                f"""INSERT INTO auth.users(email, phone, password_hash, role, whatsapp_number, created_by)
                    VALUES(lower($1), $2, $3, $4, $5, $6) RETURNING {USER_COLUMNS}""",
                str(payload.email), payload.phone, hash_password(payload.password), payload.role,
                payload.whatsapp_number, admin.id,
            )
            await write_audit_log(db, request, admin, "create", "auth.user", row["id"], {"role": payload.role})
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="A user with that email already exists") from exc
    return envelope(dict(row))


@router.get("/")
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


@router.get("/{user_id}")
async def get_user(user_id: UUID, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    row = await db.fetchrow(f"SELECT {USER_COLUMNS} FROM auth.users WHERE id=$1 AND is_deleted=false", user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return envelope(dict(row))


@router.patch("/{user_id}")
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


@router.delete("/{user_id}")
async def delete_user(user_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    async with db.transaction():
        changed = await db.fetchval(
            "UPDATE auth.users SET is_deleted=true, is_active=false, updated_at=NOW() WHERE id=$1 AND is_deleted=false RETURNING id",
            user_id,
        )
        if changed is None:
            raise HTTPException(status_code=404, detail="User not found")
        await db.execute("UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND revoked_at IS NULL", user_id)
        await write_audit_log(db, request, admin, "delete", "auth.user", user_id)
    await request.app.state.redis.delete(f"session:{user_id}", f"refresh:{user_id}")
    return envelope({"deleted": True})


@router.patch("/{user_id}/toggle-active")
async def toggle_active(user_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    async with db.transaction():
        row = await db.fetchrow(
            f"UPDATE auth.users SET is_active=NOT is_active, updated_at=NOW() WHERE id=$1 AND is_deleted=false RETURNING {USER_COLUMNS}",
            user_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        if not row["is_active"]:
            await db.execute("UPDATE auth.sessions SET revoked_at=NOW() WHERE user_id=$1 AND revoked_at IS NULL", user_id)
        await write_audit_log(db, request, admin, "toggle_active", "auth.user", user_id, {"is_active": row["is_active"]})
    if not row["is_active"]:
        await request.app.state.redis.delete(f"session:{user_id}", f"refresh:{user_id}")
    return envelope(dict(row))


@router.get("/{user_id}/permissions")
async def list_permissions(user_id: UUID, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    if not await db.fetchval("SELECT EXISTS(SELECT 1 FROM auth.users WHERE id=$1 AND is_deleted=false)", user_id):
        raise HTTPException(status_code=404, detail="User not found")
    rows = await db.fetch(
        "SELECT id, user_id, resource, actions, constraints, granted_by, created_at FROM rbac.permissions WHERE user_id=$1 ORDER BY created_at",
        user_id,
    )
    return envelope([dict(row) for row in rows])


@router.post("/{user_id}/permissions", status_code=201)
async def grant_permission(payload: PermissionCreate, user_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    if any(not action.strip() for action in payload.actions):
        raise HTTPException(status_code=422, detail="Permission actions cannot be empty")
    async with db.transaction():
        if not await db.fetchval("SELECT EXISTS(SELECT 1 FROM auth.users WHERE id=$1 AND is_deleted=false)", user_id):
            raise HTTPException(status_code=404, detail="User not found")
        row = await db.fetchrow(
            """INSERT INTO rbac.permissions(user_id, resource, actions, constraints, granted_by)
               VALUES($1, $2, $3, $4::jsonb, $5)
               RETURNING id, user_id, resource, actions, constraints, granted_by, created_at""",
            user_id, payload.resource, payload.actions, payload.constraints, admin.id,
        )
        await write_audit_log(db, request, admin, "grant", "rbac.permission", row["id"], {"user_id": str(user_id)})
    return envelope(dict(row))


@router.delete("/{user_id}/permissions/{permission_id}")
async def revoke_permission(user_id: UUID, permission_id: UUID, request: Request, admin: AdminUserDep, db: asyncpg.Connection = Depends(get_db)):
    async with db.transaction():
        removed = await db.fetchval("DELETE FROM rbac.permissions WHERE id=$1 AND user_id=$2 RETURNING id", permission_id, user_id)
        if removed is None:
            raise HTTPException(status_code=404, detail="Permission not found")
        await write_audit_log(db, request, admin, "revoke", "rbac.permission", permission_id, {"user_id": str(user_id)})
    return envelope({"deleted": True})
