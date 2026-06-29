import asyncio
import hmac
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError

from app.schemas.analytics import AlertIn, AnalyticsEventIn, HeartbeatRequest, PeopleCountIn
from app.services.sync_service import manager

router = APIRouter()


async def validate_session(websocket: WebSocket, user_id: UUID, session_token: str) -> bool:
    live = await websocket.app.state.redis.get(f"session:{user_id}")
    if live is None or not hmac.compare_digest(live, session_token):
        return False
    async with websocket.app.state.db_pool.acquire() as db:
        return bool(await db.fetchval(
            "SELECT EXISTS(SELECT 1 FROM auth.users WHERE id=$1 AND is_active=true AND is_deleted=false)", user_id
        ))


async def camera_owned(db, user_id: UUID, camera_id: UUID) -> bool:
    return bool(await db.fetchval("SELECT EXISTS(SELECT 1 FROM va.cameras WHERE id=$1 AND user_id=$2)", camera_id, user_id))


async def store_message(db, user_id: UUID, message_type: str, data: dict) -> UUID | None:
    if message_type == "event":
        item = AnalyticsEventIn.model_validate(data)
        if not await camera_owned(db, user_id, item.camera_id):
            raise PermissionError
        await db.execute(
            """INSERT INTO va.analytics_events(id, camera_id, event_type, payload, captured_image_id, synced_at, created_at)
               VALUES($1,$2,$3,$4::jsonb,$5,NOW(),$6) ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload, synced_at=NOW()""",
            item.id, item.camera_id, item.event_type, item.payload, item.captured_image_id, item.created_at,
        )
        return item.id
    if message_type == "alert":
        item = AlertIn.model_validate(data)
        if not await camera_owned(db, user_id, item.camera_id):
            raise PermissionError
        await db.execute(
            """INSERT INTO va.intrusion_alerts(id,camera_id,zone_id,captured_image_id,confidence,resolved,created_at)
               VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT(id) DO UPDATE SET resolved=EXCLUDED.resolved, confidence=EXCLUDED.confidence""",
            item.id, item.camera_id, item.zone_id, item.captured_image_id, item.confidence, item.resolved, item.created_at,
        )
        return item.id
    if message_type == "people_count":
        item = PeopleCountIn.model_validate(data)
        if not await camera_owned(db, user_id, item.camera_id):
            raise PermissionError
        await db.execute(
            """INSERT INTO va.analytics_events(id,camera_id,event_type,payload,synced_at,created_at)
               VALUES($1,$2,'people_count',jsonb_build_object('count_in',$3::int,'count_out',$4::int),NOW(),$5)
               ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload,synced_at=NOW()""",
            item.id, item.camera_id, item.count_in, item.count_out, item.timestamp,
        )
        return item.id
    if message_type == "heartbeat":
        item = HeartbeatRequest.model_validate(data)
        for key, camera_status in item.camera_statuses.items():
            camera_id = UUID(key)
            if not await camera_owned(db, user_id, camera_id):
                raise PermissionError
            await db.execute(
                "INSERT INTO va.analytics_events(camera_id,event_type,payload,synced_at,created_at) VALUES($1,'heartbeat',jsonb_build_object('status',$2::text),NOW(),$3)",
                camera_id, camera_status, item.timestamp,
            )
        return None
    raise ValueError("Unsupported message type")


@router.websocket("/sync/{user_id}")
async def sync_socket(websocket: WebSocket, user_id: UUID, session_token: str):
    if not await validate_session(websocket, user_id, session_token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid session")
        return
    await manager.connect(str(user_id), websocket)
    try:
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
                await websocket.send_json({"type": "ping"})
                continue
            message_type = message.get("type")
            if message_type == "pong":
                continue
            try:
                async with websocket.app.state.db_pool.acquire() as db, db.transaction():
                    resource_id = await store_message(db, user_id, message_type, message.get("data", {}))
                    await db.execute(
                        "INSERT INTO audit.logs(user_id,action,resource,resource_id,metadata) VALUES($1,$2,'websocket.sync',$3,$4::jsonb)",
                        user_id, f"sync_{message_type}", resource_id, {},
                    )
                await websocket.send_json({"type": "ack", "message_type": message_type, "id": str(resource_id) if resource_id else None})
            except (ValidationError, ValueError, PermissionError) as exc:
                await websocket.send_json({"type": "error", "error": "Invalid or unauthorized sync message", "details": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(str(user_id), websocket)
