from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import CurrentSession, get_container
from app.responses import envelope
from app.schemas import CameraCreate, CameraUpdate

router = APIRouter(prefix="/cameras", tags=["cameras"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_camera(
    payload: CameraCreate,
    _: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    return envelope(await container.cameras.create(payload))


@router.get("")
async def list_cameras(
    _: CurrentSession, container: Annotated[Any, Depends(get_container)]
) -> dict[str, Any]:
    return envelope(await container.cameras.list())


@router.get("/{camera_id}")
async def get_camera(
    camera_id: UUID, _: CurrentSession, container: Annotated[Any, Depends(get_container)]
) -> dict[str, Any]:
    camera = await container.cameras.get(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return envelope(camera)


@router.patch("/{camera_id}")
async def update_camera(
    camera_id: UUID,
    payload: CameraUpdate,
    _: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    camera = await container.cameras.update(camera_id, payload)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return envelope(camera)


@router.delete("/{camera_id}")
async def delete_camera(
    camera_id: UUID, _: CurrentSession, container: Annotated[Any, Depends(get_container)]
) -> dict[str, Any]:
    if not await container.cameras.delete(camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")
    return envelope({"deleted": True, "id": str(camera_id)})
