from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.schemas.analytics import AlertIn, AnalyticsEventIn
from app.services.sync_service import upsert_alert, upsert_event


class FakeDB:
    def __init__(self, result):
        self.result = result
        self.query = ""

    async def fetchval(self, query, *_):
        self.query = query
        return self.result


@pytest.mark.asyncio
async def test_event_uuid_cannot_overwrite_another_camera():
    item = AnalyticsEventIn(
        id=uuid4(), camera_id=uuid4(), event_type="motion", payload={}, created_at=datetime.now(UTC)
    )
    db = FakeDB(None)
    with pytest.raises(HTTPException) as denied:
        await upsert_event(db, item)
    assert denied.value.status_code == 409
    assert "WHERE va.analytics_events.camera_id=EXCLUDED.camera_id" in db.query


@pytest.mark.asyncio
async def test_alert_conflict_is_tenant_safe_and_resolution_is_monotonic():
    item = AlertIn(id=uuid4(), camera_id=uuid4(), resolved=False, created_at=datetime.now(UTC))
    db = FakeDB(None)
    with pytest.raises(HTTPException):
        await upsert_alert(db, item)
    assert "WHERE va.intrusion_alerts.camera_id=EXCLUDED.camera_id" in db.query
    assert "resolved=va.intrusion_alerts.resolved OR EXCLUDED.resolved" in db.query
