from typing import Any
from uuid import UUID

from fastapi import HTTPException


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[str, Any] = {}

    async def connect(self, user_id: str, websocket: Any) -> None:
        previous = self.connections.pop(user_id, None)
        if previous is not None:
            try:
                await previous.close(code=1000, reason="Replaced by a newer connection")
            except Exception:
                pass
        await websocket.accept()
        self.connections[user_id] = websocket

    def disconnect(self, user_id: str, websocket: Any) -> None:
        if self.connections.get(user_id) is websocket:
            self.connections.pop(user_id, None)

    async def close_all(self) -> None:
        connections = list(self.connections.items())
        self.connections.clear()
        for _, websocket in connections:
            try:
                await websocket.close(code=1001, reason="Server shutdown")
            except Exception:
                pass


manager = ConnectionManager()


async def upsert_event(db, item) -> UUID:
    event_id = await db.fetchval(
        """INSERT INTO va.analytics_events(id, camera_id, event_type, payload, captured_image_id, synced_at, created_at)
           VALUES($1, $2, $3, $4::jsonb, $5, NOW(), $6)
           ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload,
             captured_image_id=EXCLUDED.captured_image_id, synced_at=NOW()
           WHERE va.analytics_events.camera_id=EXCLUDED.camera_id
           RETURNING id""",
        item.id, item.camera_id, item.event_type, item.payload, item.captured_image_id,
        item.created_at,
    )
    if event_id is None:
        raise HTTPException(status_code=409, detail="Event ID belongs to another camera")
    return event_id


async def upsert_alert(db, item) -> UUID:
    alert_id = await db.fetchval(
        """INSERT INTO va.intrusion_alerts(id, camera_id, zone_id, captured_image_id, confidence, resolved, created_at)
           VALUES($1, $2, $3, $4, $5, $6, $7)
           ON CONFLICT(id) DO UPDATE SET captured_image_id=EXCLUDED.captured_image_id,
             confidence=EXCLUDED.confidence,
             resolved=va.intrusion_alerts.resolved OR EXCLUDED.resolved
           WHERE va.intrusion_alerts.camera_id=EXCLUDED.camera_id
           RETURNING id""",
        item.id, item.camera_id, item.zone_id, item.captured_image_id, item.confidence,
        item.resolved, item.created_at,
    )
    if alert_id is None:
        raise HTTPException(status_code=409, detail="Alert ID belongs to another camera")
    return alert_id


async def upsert_people_count(db, item) -> UUID:
    event_id = await db.fetchval(
        """INSERT INTO va.analytics_events(id, camera_id, event_type, payload, synced_at, created_at)
           VALUES($1, $2, 'people_count', jsonb_build_object('count_in', $3::int, 'count_out', $4::int), NOW(), $5)
           ON CONFLICT(id) DO UPDATE SET payload=EXCLUDED.payload, synced_at=NOW()
           WHERE va.analytics_events.camera_id=EXCLUDED.camera_id
           RETURNING id""",
        item.id, item.camera_id, item.count_in, item.count_out, item.timestamp,
    )
    if event_id is None:
        raise HTTPException(status_code=409, detail="Count ID belongs to another camera")
    return event_id
