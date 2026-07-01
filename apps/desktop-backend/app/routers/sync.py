from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.dependencies import CurrentSession, get_container
from app.responses import envelope

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/status")
async def sync_status(
    _: CurrentSession, container: Annotated[Any, Depends(get_container)]
) -> dict[str, Any]:
    return envelope({
        "connected": container.connected,
        "queue_count": await container.queue.count(),
        "last_checked_at": container.last_connectivity_check,
        "last_error": container.last_connectivity_error,
    })
