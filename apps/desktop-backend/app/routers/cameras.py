from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.dependencies import CurrentSession, get_container
from app.responses import envelope
from app.schemas import CameraCreate, CameraUpdate

router = APIRouter(prefix="/cameras", tags=["cameras"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_camera(
    payload: CameraCreate,
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    return envelope(await container.camera_service.create(session, payload))


@router.get("")
async def list_cameras(
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return envelope(await container.camera_service.list(session, limit, offset))


@router.get("/{camera_id}")
async def get_camera(
    camera_id: UUID, session: CurrentSession, container: Annotated[Any, Depends(get_container)]
) -> dict[str, Any]:
    camera = await container.camera_service.get(session, camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return envelope(camera)


@router.patch("/{camera_id}")
async def update_camera(
    camera_id: UUID,
    payload: CameraUpdate,
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    camera = await container.camera_service.update(session, camera_id, payload)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return envelope(camera)


@router.delete("/{camera_id}")
async def delete_camera(
    camera_id: UUID, session: CurrentSession, container: Annotated[Any, Depends(get_container)]
) -> dict[str, Any]:
    if not await container.camera_service.delete(session, camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")
    return envelope({"deleted": True, "id": str(camera_id)})
