from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.dependencies import CurrentSession, get_container
from app.responses import envelope
from app.schemas import AlertCreate, AnalyticsEventCreate, PeopleCountCreate

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/events")
async def list_events(
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return envelope(await container.analytics_service.list(session, limit, offset))


@router.post("/events", status_code=status.HTTP_201_CREATED)
async def record_event(
    payload: AnalyticsEventCreate,
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    return envelope(await container.analytics_service.event(session, payload))


@router.post("/alerts", status_code=status.HTTP_201_CREATED)
async def record_alert(
    payload: AlertCreate,
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    return envelope(await container.analytics_service.alert(session, payload))


@router.post("/people-count", status_code=status.HTTP_201_CREATED)
async def record_people_count(
    payload: PeopleCountCreate,
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    return envelope(await container.analytics_service.people_count(session, payload))
