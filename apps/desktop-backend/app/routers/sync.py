from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import CurrentSession, get_container
from app.responses import envelope

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/status")
async def sync_status(
    session: CurrentSession, container: Annotated[Any, Depends(get_container)]
) -> dict[str, Any]:
    container.authorization.require(session, "sync.read")
    return envelope({
        "connected": container.connected,
        "queue_count": await container.queue.count(),
        "dead_letter_count": await container.queue.dead_letter_count(),
        "last_checked_at": container.last_connectivity_check,
        "last_error": container.last_connectivity_error,
    })


@router.get("/dead-letters")
async def dead_letters(
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    container.authorization.require(session, "sync.read")
    return envelope(await container.queue.list_dead_letters(limit, offset))


@router.post("/dead-letters/{item_id}/retry")
async def retry_dead_letter(
    item_id: UUID, session: CurrentSession, container: Annotated[Any, Depends(get_container)]
) -> dict[str, Any]:
    container.authorization.require(session, "sync.manage")
    if not await container.queue.retry_dead_letter(item_id):
        raise HTTPException(status_code=404, detail="Dead-letter item not found")
    return envelope({"retried": True, "id": str(item_id)})


@router.delete("/dead-letters/{item_id}")
async def discard_dead_letter(
    item_id: UUID, session: CurrentSession, container: Annotated[Any, Depends(get_container)]
) -> dict[str, Any]:
    container.authorization.require(session, "sync.manage")
    if not await container.queue.discard_dead_letter(item_id):
        raise HTTPException(status_code=404, detail="Dead-letter item not found")
    return envelope({"discarded": True, "id": str(item_id)})
