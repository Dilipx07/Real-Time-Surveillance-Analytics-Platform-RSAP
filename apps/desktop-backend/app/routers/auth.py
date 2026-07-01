from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException

from app.dependencies import CurrentSession, get_container
from app.responses import envelope
from app.schemas import LoginRequest, RefreshRequest
from app.services import AuthenticationError

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
async def login(
    payload: LoginRequest, container: Annotated[Any, Depends(get_container)]
) -> dict[str, Any]:
    try:
        session = await container.auth.login(payload.email, payload.password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return envelope(session.model_dump(mode="json"))


@router.post("/logout")
async def logout(
    _: CurrentSession, container: Annotated[Any, Depends(get_container)]
) -> dict[str, Any]:
    await container.auth.logout()
    return envelope({"logged_out": True})


@router.post("/refresh")
async def refresh(
    payload: RefreshRequest,
    container: Annotated[Any, Depends(get_container)],
    session_token: Annotated[str | None, Header(alias="X-Session-Token")] = None,
) -> dict[str, Any]:
    if not session_token:
        raise HTTPException(status_code=401, detail="Session token is required")
    try:
        session = await container.auth.refresh(payload.refresh_token, session_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return envelope(session.model_dump(mode="json"))


@router.get("/me")
async def me(session: CurrentSession) -> dict[str, Any]:
    return envelope(session.user)


@router.get("/license-status")
async def license_status(
    container: Annotated[Any, Depends(get_container)],
) -> dict[str, Any]:
    return envelope(await container.auth.license_status())
