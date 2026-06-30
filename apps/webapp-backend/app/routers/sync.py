from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from app.dependencies import CurrentUserDep, get_db
from app.middleware.audit import write_audit_log
from app.responses import SuccessEnvelope, envelope
from app.schemas.analytics import HeartbeatRequest, SyncAlertsRequest, SyncEventsRequest, SyncPeopleCountsRequest
from app.services.rbac_service import authorize
from app.services.sync_service import upsert_alert, upsert_event, upsert_people_count

router = APIRouter()


async def ensure_owned_cameras(db: asyncpg.Connection, user, camera_ids: set[UUID]) -> None:
    if not camera_ids:
        return
    rows = await db.fetch("SELECT id FROM va.cameras WHERE user_id=$1 AND id=ANY($2::uuid[])", user.id, list(camera_ids))
    if {row["id"] for row in rows} != camera_ids:
        raise HTTPException(status_code=403, detail="One or more cameras are not assigned to this user")


@router.post("/events", response_model=SuccessEnvelope)
async def sync_events(payload: SyncEventsRequest, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    authorize(user, "sync", "write", owner_id=user.id)
    await ensure_owned_cameras(db, user, {item.camera_id for item in payload.events})
    async with db.transaction():
        for item in payload.events:
            await upsert_event(db, item)
        await write_audit_log(db, request, user, "sync_events", "va.analytics_event", metadata={
            "batch_id": str(uuid4()), "resource_ids": [str(item.id) for item in payload.events]
        })
    return envelope({"accepted": len(payload.events)})


@router.post("/alerts", response_model=SuccessEnvelope)
async def sync_alerts(payload: SyncAlertsRequest, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    authorize(user, "sync", "write", owner_id=user.id)
    await ensure_owned_cameras(db, user, {item.camera_id for item in payload.alerts})
    async with db.transaction():
        for item in payload.alerts:
            await upsert_alert(db, item)
        await write_audit_log(db, request, user, "sync_alerts", "va.intrusion_alert", metadata={
            "batch_id": str(uuid4()), "resource_ids": [str(item.id) for item in payload.alerts]
        })
    return envelope({"accepted": len(payload.alerts)})


@router.post("/people-count", response_model=SuccessEnvelope)
async def sync_people_count(payload: SyncPeopleCountsRequest, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    authorize(user, "sync", "write", owner_id=user.id)
    await ensure_owned_cameras(db, user, {item.camera_id for item in payload.snapshots})
    async with db.transaction():
        for item in payload.snapshots:
            await upsert_people_count(db, item)
        await write_audit_log(db, request, user, "sync_people_count", "va.analytics_event", metadata={
            "batch_id": str(uuid4()), "resource_ids": [str(item.id) for item in payload.snapshots]
        })
    return envelope({"accepted": len(payload.snapshots)})


@router.post("/heartbeat", response_model=SuccessEnvelope)
async def heartbeat(payload: HeartbeatRequest, request: Request, user: CurrentUserDep, db: asyncpg.Connection = Depends(get_db)):
    authorize(user, "sync", "write", owner_id=user.id)
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
        await write_audit_log(db, request, user, "heartbeat", "va.camera", metadata={
            "batch_id": str(uuid4()), "resource_ids": [str(value) for value in statuses]
        })
    return envelope({"accepted": len(statuses), "server_received": True})
