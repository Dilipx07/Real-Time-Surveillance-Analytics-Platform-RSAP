from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.dependencies import CurrentSession, get_container
from app.responses import envelope

router = APIRouter(prefix="/persons", tags=["persons"])


@router.get("")
async def list_people(
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return envelope(await container.person_service.list(session, limit, offset))
