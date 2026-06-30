from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.auth import verify_service_token
from app.config import Settings, get_settings
from app.dependencies import get_storage
from app.minio_client import StorageService
from app.presigned import clamp_presigned_expiry
from app.schemas import PresignedUrlResponse
from app.validation import validate_uuid4


router = APIRouter(
    prefix="/files",
    tags=["downloads"],
    dependencies=[Depends(verify_service_token)],
)


@router.get("/{bucket}/{file_id}/presigned", response_model=PresignedUrlResponse)
async def get_presigned_url(
    bucket: str,
    file_id: UUID,
    expires: int = Query(default=3600, ge=60, le=604800),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> PresignedUrlResponse:
    file_id = validate_uuid4(file_id)
    expires = clamp_presigned_expiry(settings, expires)
    url = await run_in_threadpool(storage.presigned_url, bucket, file_id, expires)
    return PresignedUrlResponse(file_id=file_id, bucket=bucket, url=url, expires=expires)


@router.get("/{bucket}/{file_id}", response_class=RedirectResponse)
async def redirect_to_file(
    bucket: str,
    file_id: UUID,
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    file_id = validate_uuid4(file_id)
    expiry = clamp_presigned_expiry(settings)
    url = await run_in_threadpool(storage.presigned_url, bucket, file_id, expiry)
    return RedirectResponse(url=url, status_code=307)
