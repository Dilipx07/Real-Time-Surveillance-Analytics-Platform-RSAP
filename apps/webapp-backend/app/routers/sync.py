from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import CurrentUserDep, get_db
from app.middleware.audit import write_audit_log
from app.responses import envelope
from app.schemas.analytics import HeartbeatRequest, SyncAlertsRequest, SyncEventsRequest, SyncPeopleCountsRequest

router = APIRouter()


async def ensure_owned_cameras(db: asyncpg.Connection, user, camera_ids: set[UUID]) -> None:
    if not camera_ids:
        return
    rows = await db.fetch("SELECT id FROM va.cameras WHERE user_id=$1 AND id=ANY($2::uuid[])", user.id, list(camera_ids))
    if {row["id"] for row in rows} != camera_ids:
        raise HTTPException(status_code=403, detail="One or more cameras are not assigned to this user")


@router.post("/events")
async def sync_events(payload: SyncEventsRequest, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    await ensure_owned_cameras(db, user, {item.camera_id for item in payload.events})
    async with db.transaction():
        for item in payload.events:
            await db.execute(
                """INSERT INTO va.analytics_events(id, camera_id, event_type, payload, captured_image_id, synced_at, created_at)
                   VALUES($1, $2, $3, $4::jsonb, $5, NOW(), $6)
                   ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload, captured_image_id=EXCLUDED.captured_image_id, synced_at=NOW()""",
                item.id, item.camera_id, item.event_type, item.payload, item.captured_image_id, item.created_at,
            )
        await write_audit_log(db, request, user, "sync_events", "va.analytics_event", metadata={"count": len(payload.events)})
    return envelope({"accepted": len(payload.events)})


@router.post("/alerts")
async def sync_alerts(payload: SyncAlertsRequest, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    await ensure_owned_cameras(db, user, {item.camera_id for item in payload.alerts})
    async with db.transaction():
        for item in payload.alerts:
            await db.execute(
                """INSERT INTO va.intrusion_alerts(id, camera_id, zone_id, captured_image_id, confidence, resolved, created_at)
                   VALUES($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT(id) DO UPDATE SET captured_image_id=EXCLUDED.captured_image_id,
                       confidence=EXCLUDED.confidence, resolved=EXCLUDED.resolved""",
                item.id, item.camera_id, item.zone_id, item.captured_image_id,
                item.confidence, item.resolved, item.created_at,
            )
        await write_audit_log(db, request, user, "sync_alerts", "va.intrusion_alert", metadata={"count": len(payload.alerts)})
    return envelope({"accepted": len(payload.alerts)})


@router.post("/people-count")
async def sync_people_count(payload: SyncPeopleCountsRequest, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    await ensure_owned_cameras(db, user, {item.camera_id for item in payload.snapshots})
    async with db.transaction():
        for item in payload.snapshots:
            await db.execute(
                """INSERT INTO va.analytics_events(id, camera_id, event_type, payload, synced_at, created_at)
                   VALUES($1, $2, 'people_count', jsonb_build_object('count_in', $3::int, 'count_out', $4::int), NOW(), $5)
                   ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload, synced_at=NOW()""",
                item.id, item.camera_id, item.count_in, item.count_out, item.timestamp,
            )
        await write_audit_log(db, request, user, "sync_people_count", "va.analytics_event", metadata={"count": len(payload.snapshots)})
    return envelope({"accepted": len(payload.snapshots)})


@router.post("/heartbeat")
async def heartbeat(payload: HeartbeatRequest, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    try:
        statuses = {UUID(key): value for key, value in payload.camera_statuses.items()}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Camera status keys must be UUIDs") from exc
    await ensure_owned_cameras(db, user, set(statuses))
    async with db.transaction():
        for camera_id, camera_status in statuses.items():
            await db.execute(
                "INSERT INTO va.analytics_events(camera_id, event_type, payload, synced_at, created_at) VALUES($1, 'heartbeat', jsonb_build_object('status', $2::text), NOW(), $3)",
                camera_id, camera_status, payload.timestamp,
            )
        await write_audit_log(db, request, user, "heartbeat", "va.camera", metadata={"camera_count": len(statuses)})
    return envelope({"accepted": len(statuses), "server_received": True})
