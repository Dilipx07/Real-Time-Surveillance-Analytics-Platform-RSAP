"""FastAPI dependencies for local dual-token authentication."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.schemas import LocalSession
from app.services import AuthenticationError, AuthService

bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Any:
    return request.app.state.container


async def current_session(
    container: Annotated[Any, Depends(get_container)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session_token: Annotated[str | None, Header(alias="X-Session-Token")] = None,
) -> LocalSession:
    if credentials is None or credentials.scheme.lower() != "bearer" or not session_token:
        raise HTTPException(status_code=401, detail="Bearer and session tokens are required")
    auth: AuthService = container.auth
    try:
        return await auth.authenticate(credentials.credentials, session_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


CurrentSession = Annotated[LocalSession, Depends(current_session)]

