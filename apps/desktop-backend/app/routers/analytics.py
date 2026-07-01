from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.dependencies import CurrentSession, get_container
from app.responses import envelope
from app.schemas import AlertCreate, AnalyticsEventCreate, PeopleCountCreate

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/events")
async def list_events(
    _: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return envelope(await container.analytics.list_events(limit, offset))


@router.post("/events", status_code=status.HTTP_201_CREATED)
async def record_event(
    payload: AnalyticsEventCreate,
    _: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    return envelope(await container.analytics.add_event(payload))


@router.post("/alerts", status_code=status.HTTP_201_CREATED)
async def record_alert(
    payload: AlertCreate,
    _: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    return envelope(await container.analytics.add_alert(payload))


@router.post("/people-count", status_code=status.HTTP_201_CREATED)
async def record_people_count(
    payload: PeopleCountCreate,
    _: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    return envelope(await container.analytics.add_people_count(payload))
