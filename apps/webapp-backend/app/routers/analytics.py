from datetime import UTC, datetime, time
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.dependencies import CurrentUserDep, get_db
from app.middleware.audit import write_audit_log
from app.responses import PaginatedEnvelope, SuccessEnvelope, envelope
from app.schemas.common import AwareDatetime
from app.services.rbac_service import authorize

router = APIRouter()


def camera_scope(user, alias: str = "c") -> tuple[str, list]:
    if user.role in {"admin", "super_admin"}:
        return "TRUE", []
    return f"{alias}.user_id=$1", [user.id]


@router.get("/events", response_model=PaginatedEnvelope)
async def list_events(
    user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db), page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100), camera_id: UUID | None = None,
    event_type: str | None = None, date_from: AwareDatetime | None = None,
    date_to: AwareDatetime | None = None,
):
    authorize(user, "analytics", "read", owner_id=user.id, resource_id=camera_id)
    scope, args = camera_scope(user)
    conditions = [scope]
    for column, value in (("e.camera_id", camera_id), ("e.event_type", event_type), ("e.created_at >=", date_from), ("e.created_at <=", date_to)):
        if value is not None:
            args.append(value)
            if column.endswith((">=", "<=")):
                conditions.append(f"{column} ${len(args)}")
            else:
                conditions.append(f"{column}=${len(args)}")
    where = " AND ".join(conditions)
    base = "FROM va.analytics_events e JOIN va.cameras c ON c.id=e.camera_id"
    total = await db.fetchval(f"SELECT count(*) {base} WHERE {where}", *args)
    args.extend([page_size, (page - 1) * page_size])
    rows = await db.fetch(
        f"""SELECT e.id, e.camera_id, c.name AS camera_name, e.event_type, e.payload,
                   e.captured_image_id, e.synced_at, e.created_at {base} WHERE {where}
            ORDER BY e.created_at DESC LIMIT ${len(args)-1} OFFSET ${len(args)}""", *args,
    )
    return envelope({"items": [dict(row) for row in rows], "page": page, "page_size": page_size, "total": total})


@router.get("/events/{event_id}", response_model=SuccessEnvelope)
async def get_event(event_id: UUID, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    scope, args = camera_scope(user)
    args.append(event_id)
    row = await db.fetchrow(
        f"""SELECT e.id, e.camera_id, c.name AS camera_name, e.event_type, e.payload,
                   e.captured_image_id, e.synced_at, e.created_at
            FROM va.analytics_events e JOIN va.cameras c ON c.id=e.camera_id
            WHERE {scope} AND e.id=${len(args)}""", *args,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Analytics event not found")
    authorize(user, "analytics", "read", owner_id=user.id, resource_id=row["camera_id"])
    return envelope(dict(row))


@router.get("/alerts", response_model=PaginatedEnvelope)
async def list_alerts(
    user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db), page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100), camera_id: UUID | None = None, resolved: bool | None = None,
):
    authorize(user, "analytics", "read", owner_id=user.id, resource_id=camera_id)
    scope, args = camera_scope(user)
    conditions = [scope]
    if camera_id is not None:
        args.append(camera_id); conditions.append(f"a.camera_id=${len(args)}")
    if resolved is not None:
        args.append(resolved); conditions.append(f"a.resolved=${len(args)}")
    where = " AND ".join(conditions)
    base = "FROM va.intrusion_alerts a JOIN va.cameras c ON c.id=a.camera_id"
    total = await db.fetchval(f"SELECT count(*) {base} WHERE {where}", *args)
    args.extend([page_size, (page - 1) * page_size])
    rows = await db.fetch(
        f"""SELECT a.id, a.camera_id, c.name AS camera_name, a.zone_id, a.captured_image_id,
                   a.confidence, a.resolved, a.created_at {base} WHERE {where}
            ORDER BY a.created_at DESC LIMIT ${len(args)-1} OFFSET ${len(args)}""", *args,
    )
    return envelope({"items": [dict(row) for row in rows], "page": page, "page_size": page_size, "total": total})


@router.patch("/alerts/{alert_id}/resolve", response_model=SuccessEnvelope)
async def resolve_alert(alert_id: UUID, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    scope, args = camera_scope(user)
    args.append(alert_id)
    async with db.transaction():
        camera_id = await db.fetchval(
            f"""SELECT a.camera_id FROM va.intrusion_alerts a JOIN va.cameras c ON c.id=a.camera_id
                WHERE {scope} AND a.id=${len(args)} FOR UPDATE OF a""",
            *args,
        )
        if camera_id is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        authorize(user, "analytics", "resolve", owner_id=user.id, resource_id=camera_id)
        row = await db.fetchrow(
            f"""UPDATE va.intrusion_alerts a SET resolved=true FROM va.cameras c
                WHERE a.camera_id=c.id AND {scope} AND a.id=${len(args)}
                RETURNING a.id, a.camera_id, a.zone_id, a.captured_image_id, a.confidence, a.resolved, a.created_at""",
            *args,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        await write_audit_log(db, request, user, "resolve", "va.intrusion_alert", alert_id)
    return envelope(dict(row))


@router.get("/dashboard", response_model=SuccessEnvelope)
async def dashboard(user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    authorize(user, "analytics", "read", owner_id=user.id)
    scope, args = camera_scope(user)
    start = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    total_persons = await db.fetchval("SELECT count(*) FROM events.registered_persons")
    today_entries = await db.fetchval("SELECT count(*) FROM events.registered_persons WHERE entry_time >= $1", start)
    active_cameras = await db.fetchval(f"SELECT count(*) FROM va.cameras c WHERE {scope} AND c.is_active=true", *args)
    open_alerts = await db.fetchval(
        f"SELECT count(*) FROM va.intrusion_alerts a JOIN va.cameras c ON c.id=a.camera_id WHERE {scope} AND a.resolved=false",
        *args,
    )
    return envelope({
        "total_persons": total_persons, "today_entries": today_entries,
        "active_cameras": active_cameras, "open_alerts": open_alerts,
    })


@router.get("/people-count", response_model=SuccessEnvelope)
async def people_count(
    user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db), camera_id: UUID | None = None,
    date_from: AwareDatetime | None = None, date_to: AwareDatetime | None = None,
):
    authorize(user, "analytics", "read", owner_id=user.id, resource_id=camera_id)
    scope, args = camera_scope(user)
    conditions = [scope, "e.event_type='people_count'"]
    for column, value in (("e.camera_id", camera_id), ("e.created_at >=", date_from), ("e.created_at <=", date_to)):
        if value is not None:
            args.append(value)
            conditions.append(f"{column} ${len(args)}" if column.endswith((">=", "<=")) else f"{column}=${len(args)}")
    rows = await db.fetch(
        f"""SELECT e.camera_id, c.name AS camera_name, (e.payload->>'count_in')::int AS count_in,
                   (e.payload->>'count_out')::int AS count_out, e.created_at
            FROM va.analytics_events e JOIN va.cameras c ON c.id=e.camera_id
            WHERE {' AND '.join(conditions)} ORDER BY e.created_at""", *args,
    )
    return envelope([dict(row) for row in rows])
