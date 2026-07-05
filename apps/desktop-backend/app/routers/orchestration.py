"""Authenticated Agent-4 boundary for process-local camera orchestration."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import CurrentSession, get_container
from app.orchestration import OrchestrationError
from app.responses import envelope

router = APIRouter(prefix="/orchestration", tags=["orchestration"])


@router.get("/health")
async def orchestration_health(
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    container.authorization.require(session, "camera.read")
    return envelope(
        {
            "service": container.orchestration.runtime_status(),
            **container.camera_manager.health().to_dict(),
        }
    )


@router.get("/cameras")
async def orchestration_cameras(
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    container.authorization.require(session, "camera.read")
    statuses = container.camera_manager.statuses()
    return envelope([statuses[key].to_dict() for key in sorted(statuses)])


@router.get("/cameras/{camera_id}/status")
async def orchestration_status(
    camera_id: UUID,
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    if not await container.camera_catalog.exists(camera_id, session):
        raise HTTPException(status_code=404, detail="Camera not found")
    status = container.camera_manager.get_status(str(camera_id))
    if status is None:
        raise HTTPException(status_code=404, detail="Camera status is unavailable")
    return envelope(status.to_dict())


@router.post("/cameras/{camera_id}/start")
async def orchestration_start(
    camera_id: UUID,
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    container.authorization.require(session, "camera.update")
    definition = await container.camera_catalog.definition_for(camera_id, session)
    if definition is None:
        raise HTTPException(status_code=404, detail="Active camera not found")
    try:
        result = await container.camera_manager.start_camera(definition)
    except OrchestrationError as error:
        raise _operation_error(error) from None
    return envelope(result.to_dict())


@router.post("/cameras/{camera_id}/stop")
async def orchestration_stop(
    camera_id: UUID,
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    container.authorization.require(session, "camera.update")
    if not await container.camera_catalog.exists(camera_id, session):
        raise HTTPException(status_code=404, detail="Camera not found")
    try:
        result = await container.camera_manager.stop_camera(str(camera_id))
    except OrchestrationError as error:
        raise _operation_error(error) from None
    return envelope(result.to_dict())


@router.post("/cameras/{camera_id}/restart")
async def orchestration_restart(
    camera_id: UUID,
    session: CurrentSession,
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    container.authorization.require(session, "camera.update")
    definition = await container.camera_catalog.definition_for(camera_id, session)
    if definition is None:
        raise HTTPException(status_code=404, detail="Active camera not found")
    try:
        result = await container.camera_manager.restart_camera(definition)
    except OrchestrationError as error:
        raise _operation_error(error) from None
    return envelope(result.to_dict())


def _operation_error(error: OrchestrationError) -> HTTPException:
    status_code = 409 if error.category.value in {
        "capacity",
        "configuration",
        "shutdown",
    } else 500
    return HTTPException(status_code=status_code, detail=error.public_message)
