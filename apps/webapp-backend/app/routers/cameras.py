from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.dependencies import CurrentUserDep, get_db
from app.encryption import decrypt_text, encrypt_text
from app.middleware.audit import write_audit_log
from app.responses import PaginatedEnvelope, SuccessEnvelope, envelope
from app.schemas.camera import AnalyticsConfigUpdate, CameraCreate, CameraUpdate
from app.services.rbac_service import authorize

router = APIRouter()
CAMERA_COLUMNS = "id, user_id, name, stream_url_encrypted, stream_type, location_label, analytics_config, zones, is_active, created_at, updated_at"


def public_camera(row: asyncpg.Record) -> dict:
    result = dict(row)
    result["stream_url"] = decrypt_text(result.pop("stream_url_encrypted"))
    return result


def camera_create_matches(row: asyncpg.Record, payload: CameraCreate) -> bool:
    """Return whether an existing row is the exact idempotent create result."""
    return (
        row["name"] == payload.name
        and decrypt_text(row["stream_url_encrypted"]) == payload.stream_url
        and row["stream_type"] == payload.stream_type
        and row["location_label"] == payload.location_label
        and row["analytics_config"] == payload.analytics_config
        and row["zones"] == payload.zones
    )


@router.post("/", status_code=201, response_model=SuccessEnvelope)
async def create_camera(payload: CameraCreate, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    authorize(user, "cameras", "create", owner_id=user.id)
    async with db.transaction():
        await db.fetchrow("SELECT id FROM auth.users WHERE id=$1 FOR UPDATE", user.id)
        row = await db.fetchrow(f"SELECT {CAMERA_COLUMNS} FROM va.cameras WHERE id=$1", payload.id)
        if row is not None and (
            row["user_id"] != user.id or not camera_create_matches(row, payload)
        ):
            raise HTTPException(status_code=409, detail="Camera ID is already in use")
        if row is None:
            license_row = await db.fetchrow(
                """SELECT max_cameras FROM rbac.licenses WHERE user_id=$1 AND is_active=true
                   AND valid_from <= NOW() AND valid_until > NOW()
                   ORDER BY valid_until DESC LIMIT 1 FOR UPDATE""",
                user.id,
            )
            if license_row is None:
                raise HTTPException(status_code=403, detail="No active license")
            count = await db.fetchval("SELECT count(*) FROM va.cameras WHERE user_id=$1", user.id)
            if count >= license_row["max_cameras"]:
                raise HTTPException(status_code=409, detail="License camera limit reached")
            row = await db.fetchrow(
                f"""INSERT INTO va.cameras(id, user_id, name, stream_url_encrypted, stream_type, location_label, analytics_config, zones)
                    VALUES($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb)
                    ON CONFLICT (id) DO NOTHING RETURNING {CAMERA_COLUMNS}""",
                payload.id, user.id, payload.name, encrypt_text(payload.stream_url),
                payload.stream_type, payload.location_label, payload.analytics_config, payload.zones,
            )
            if row is None:
                row = await db.fetchrow(f"SELECT {CAMERA_COLUMNS} FROM va.cameras WHERE id=$1", payload.id)
                if (
                    row is None
                    or row["user_id"] != user.id
                    or not camera_create_matches(row, payload)
                ):
                    raise HTTPException(status_code=409, detail="Camera ID is already in use")
            else:
                await write_audit_log(db, request, user, "create", "va.camera", row["id"])
    return envelope(public_camera(row))


@router.get("/", response_model=PaginatedEnvelope)
async def list_cameras(user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    permission = authorize(user, "cameras", "read", owner_id=user.id)
    allowed_ids = (permission or {}).get("constraints", {}).get("allowed_resource_ids")
    ids = [UUID(str(value)) for value in allowed_ids] if allowed_ids is not None else None
    total = await db.fetchval(
        "SELECT count(*) FROM va.cameras WHERE user_id=$1 AND ($2::uuid[] IS NULL OR id=ANY($2))",
        user.id, ids,
    )
    rows = await db.fetch(
        f"SELECT {CAMERA_COLUMNS} FROM va.cameras WHERE user_id=$1 AND ($2::uuid[] IS NULL OR id=ANY($2)) ORDER BY created_at DESC LIMIT $3 OFFSET $4",
        user.id, ids, page_size, (page - 1) * page_size,
    )
    return envelope({"items": [public_camera(row) for row in rows], "page": page, "page_size": page_size, "total": total})


@router.get("/{camera_id}", response_model=SuccessEnvelope)
async def get_camera(camera_id: UUID, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    row = await db.fetchrow(f"SELECT {CAMERA_COLUMNS} FROM va.cameras WHERE id=$1 AND user_id=$2", camera_id, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    authorize(user, "cameras", "read", owner_id=row["user_id"], resource_id=camera_id)
    return envelope(public_camera(row))


@router.patch("/{camera_id}", response_model=SuccessEnvelope)
async def update_camera(payload: CameraUpdate, camera_id: UUID, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    authorize(user, "cameras", "update", owner_id=user.id, resource_id=camera_id)
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="At least one field is required")
    if "stream_url" in values:
        values["stream_url_encrypted"] = encrypt_text(values.pop("stream_url"))
    assignments, args = [], []
    for key, value in values.items():
        args.append(value)
        assignments.append(f"{key}=${len(args)}")
    args.extend([camera_id, user.id])
    async with db.transaction():
        row = await db.fetchrow(
            f"UPDATE va.cameras SET {', '.join(assignments)}, updated_at=NOW() WHERE id=${len(args)-1} AND user_id=${len(args)} RETURNING {CAMERA_COLUMNS}",
            *args,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Camera not found")
        await write_audit_log(db, request, user, "update", "va.camera", camera_id, {"fields": list(values)})
    return envelope(public_camera(row))


@router.delete("/{camera_id}", response_model=SuccessEnvelope)
async def delete_camera(camera_id: UUID, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    authorize(user, "cameras", "delete", owner_id=user.id, resource_id=camera_id)
    try:
        async with db.transaction():
            deleted = await db.fetchval("DELETE FROM va.cameras WHERE id=$1 AND user_id=$2 RETURNING id", camera_id, user.id)
            if deleted is None:
                raise HTTPException(status_code=404, detail="Camera not found")
            await write_audit_log(db, request, user, "delete", "va.camera", camera_id)
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(status_code=409, detail="Camera has analytics history and cannot be deleted") from exc
    return envelope({"deleted": True})


@router.patch("/{camera_id}/analytics-config", response_model=SuccessEnvelope)
async def update_analytics_config(payload: AnalyticsConfigUpdate, camera_id: UUID, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    authorize(user, "cameras", "configure", owner_id=user.id, resource_id=camera_id)
    async with db.transaction():
        row = await db.fetchrow(
            f"""UPDATE va.cameras SET analytics_config=$1::jsonb, zones=$2::jsonb, updated_at=NOW()
                WHERE id=$3 AND user_id=$4 RETURNING {CAMERA_COLUMNS}""",
            payload.analytics_config, payload.zones, camera_id, user.id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Camera not found")
        await write_audit_log(db, request, user, "configure_analytics", "va.camera", camera_id)
    return envelope(public_camera(row))
