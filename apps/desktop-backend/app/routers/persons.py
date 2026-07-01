from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.dependencies import CurrentSession, get_container
from app.responses import envelope

router = APIRouter(prefix="/persons", tags=["persons"])


@router.get("")
async def list_people(
    _: CurrentSession, container: Annotated[Any, Depends(get_container)]
) -> dict[str, Any]:
    return envelope(await container.persons.list())
