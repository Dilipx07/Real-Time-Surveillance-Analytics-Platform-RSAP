from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, File, UploadFile
from starlette.concurrency import run_in_threadpool

from app.auth import verify_service_token
from app.config import Settings, get_settings
from app.dependencies import get_storage
from app.minio_client import StorageService
from app.presigned import clamp_presigned_expiry
from app.schemas import UploadResponse
from app.validation import (
    DOCUMENT_CONTENT_TYPES,
    IMAGE_CONTENT_TYPES,
    read_validated_upload,
    validate_category,
)


router = APIRouter(
    prefix="/upload",
    tags=["uploads"],
    dependencies=[Depends(verify_service_token)],
)


@router.post("/face", response_model=UploadResponse, status_code=201)
async def upload_face(
    file: UploadFile = File(...),
    storage: StorageService = Depends(get_storage),
    settings: Settings = Depends(get_settings),
) -> UploadResponse:
    data, content_type = await read_validated_upload(file, set(IMAGE_CONTENT_TYPES))
    file_id = uuid4()
    object_name = f"{file_id}.jpg"
    bucket = storage.settings.minio_bucket_faces
    await run_in_threadpool(storage.upload, bucket, object_name, data, content_type)
    expiry = clamp_presigned_expiry(settings)
    url = await run_in_threadpool(storage.presigned_url, bucket, file_id, expiry)
    return UploadResponse(
        file_id=file_id,
        bucket=bucket,
        object_name=object_name,
        content_type=content_type,
        size=len(data),
        url=url,
    )


@router.post("/capture", response_model=UploadResponse, status_code=201)
async def upload_capture(
    file: UploadFile = File(...),
    storage: StorageService = Depends(get_storage),
) -> UploadResponse:
    data, content_type = await read_validated_upload(file, set(IMAGE_CONTENT_TYPES))
    file_id = uuid4()
    object_name = f"{datetime.now(UTC).date().isoformat()}/{file_id}.jpg"
    bucket = storage.settings.minio_bucket_captures
    await run_in_threadpool(storage.upload, bucket, object_name, data, content_type)
    return UploadResponse(
        file_id=file_id,
        bucket=bucket,
        object_name=object_name,
        content_type=content_type,
        size=len(data),
    )


@router.post("/document/{category}", response_model=UploadResponse, status_code=201)
async def upload_document(
    category: str,
    file: UploadFile = File(...),
    storage: StorageService = Depends(get_storage),
) -> UploadResponse:
    category = validate_category(category)
    data, content_type = await read_validated_upload(file, set(DOCUMENT_CONTENT_TYPES))
    file_id = uuid4()
    object_name = f"{category}/{file_id}{DOCUMENT_CONTENT_TYPES[content_type]}"
    bucket = storage.settings.minio_bucket_documents
    await run_in_threadpool(storage.upload, bucket, object_name, data, content_type)
    return UploadResponse(
        file_id=file_id,
        bucket=bucket,
        object_name=object_name,
        content_type=content_type,
        size=len(data),
    )
