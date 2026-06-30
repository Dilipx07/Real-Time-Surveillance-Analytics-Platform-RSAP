import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from app.schemas.analytics import AlertIn, AnalyticsEventIn, HeartbeatRequest, PeopleCountIn
from app.services.sync_service import manager
from app.models.user import CurrentUser
from app.services.rbac_service import authorize
from app.services.session_service import validate_session_state
from app.services.sync_service import upsert_alert, upsert_event, upsert_people_count

router = APIRouter()


async def validate_session(
    websocket: WebSocket, user_id: UUID, session_token: str
) -> CurrentUser | None:
    async with websocket.app.state.db_pool.acquire() as db:
        validated = await validate_session_state(
            websocket.app.state.redis, db, user_id, session_token
        )
    if validated is None:
        return None
    state, row, permissions = validated
    user = CurrentUser(
        id=row["id"], email=row["email"], role=row["role"], session_id=state["sid"],
        license_id=UUID(state["license_id"]), permissions=permissions,
    )
    try:
        authorize(user, "sync", "write", owner_id=user.id)
    except HTTPException:
        return None
    return user


async def camera_owned(db, user_id: UUID, camera_id: UUID) -> bool:
    return bool(await db.fetchval("SELECT EXISTS(SELECT 1 FROM va.cameras WHERE id=$1 AND user_id=$2)", camera_id, user_id))


async def store_message(
    db, user: CurrentUser, message_type: str, data: dict
) -> tuple[UUID | None, list[UUID]]:
    user_id = user.id
    if message_type == "event":
        item = AnalyticsEventIn.model_validate(data)
        if not await camera_owned(db, user_id, item.camera_id):
            raise PermissionError
        resource_id = await upsert_event(db, item)
        return resource_id, [resource_id]
    if message_type == "alert":
        item = AlertIn.model_validate(data)
        if not await camera_owned(db, user_id, item.camera_id):
            raise PermissionError
        resource_id = await upsert_alert(db, item)
        return resource_id, [resource_id]
    if message_type == "people_count":
        item = PeopleCountIn.model_validate(data)
        if not await camera_owned(db, user_id, item.camera_id):
            raise PermissionError
        resource_id = await upsert_people_count(db, item)
        return resource_id, [resource_id]
    if message_type == "heartbeat":
        item = HeartbeatRequest.model_validate(data)
        resource_ids = []
        for key, camera_status in item.camera_statuses.items():
            camera_id = UUID(key)
            if not await camera_owned(db, user_id, camera_id):
                raise PermissionError
            event_id = await db.fetchval(
                "INSERT INTO va.analytics_events(camera_id,event_type,payload,synced_at,created_at) VALUES($1,'heartbeat',jsonb_build_object('status',$2::text),NOW(),$3) RETURNING id",
                camera_id, camera_status, item.timestamp,
            )
            resource_ids.append(event_id)
        return None, resource_ids
    raise ValueError("Unsupported message type")


@router.websocket("/sync/{user_id}")
async def sync_socket(websocket: WebSocket, user_id: UUID, session_token: str):
    user = await validate_session(websocket, user_id, session_token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid session")
        return
    registered = False
    try:
        await manager.connect(str(user_id), websocket)
        registered = True
        async with websocket.app.state.db_pool.acquire() as db:
            configs = await db.fetch(
                "SELECT id, analytics_config, zones, updated_at FROM va.cameras WHERE user_id=$1 AND is_active=true",
                user_id,
            )
        await websocket.send_json(jsonable_encoder({"type": "config_update", "data": [dict(row) for row in configs]}))
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=30)
            except TimeoutError:
                user = await validate_session(websocket, user_id, session_token)
                if user is None:
                    await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authorization expired")
                    return
                await websocket.send_json({"type": "ping"})
                continue
            user = await validate_session(websocket, user_id, session_token)
            if user is None:
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Authorization expired")
                return
            if not isinstance(message, dict):
                await websocket.send_json({"type": "error", "error": "Message must be an object"})
                continue
            message_type = message.get("type")
            if message_type == "pong":
                continue
            try:
                async with websocket.app.state.db_pool.acquire() as db, db.transaction():
                    resource_id, resource_ids = await store_message(
                        db, user, message_type, message.get("data", {})
                    )
                    await db.execute(
                        "INSERT INTO audit.logs(user_id,action,resource,resource_id,metadata) VALUES($1,$2,'websocket.sync',$3,$4::jsonb)",
                        user_id, f"sync_{message_type}", resource_id,
                        {"resource_ids": [str(item) for item in resource_ids]},
                    )
                await websocket.send_json({"type": "ack", "message_type": message_type, "id": str(resource_id) if resource_id else None})
            except (ValidationError, ValueError, PermissionError, HTTPException) as exc:
                await websocket.send_json({"type": "error", "error": "Invalid or unauthorized sync message", "details": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        if registered:
            manager.disconnect(str(user_id), websocket)
