from uuid import UUID

import urllib3
from fastapi import APIRouter, Depends, Query
from minio.error import S3Error
from starlette.concurrency import run_in_threadpool

from app.auth import verify_service_token
from app.dependencies import get_storage
from app.minio_client import InvalidBucketError, ObjectNotFoundError, StorageService
from app.schemas import (
    BatchDeleteFailure,
    BatchDeleteRequest,
    BatchDeleteResponse,
    DeleteResponse,
    FileListResponse,
)
from app.validation import validate_uuid4


router = APIRouter(
    prefix="/files",
    tags=["file management"],
    dependencies=[Depends(verify_service_token)],
)


@router.get("/{bucket}", response_model=FileListResponse)
async def list_files(
    bucket: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    storage: StorageService = Depends(get_storage),
) -> FileListResponse:
    items, has_more = await run_in_threadpool(storage.list_files, bucket, page, page_size)
    return FileListResponse(
        bucket=bucket,
        page=page,
        page_size=page_size,
        items=items,
        has_more=has_more,
    )


@router.delete("/{bucket}/{file_id}", response_model=DeleteResponse)
async def delete_file(
    bucket: str,
    file_id: UUID,
    storage: StorageService = Depends(get_storage),
) -> DeleteResponse:
    file_id = validate_uuid4(file_id)
    await run_in_threadpool(storage.remove, bucket, file_id)
    return DeleteResponse(file_id=file_id, bucket=bucket)


@router.post("/batch-delete", response_model=BatchDeleteResponse)
async def batch_delete(
    request: BatchDeleteRequest,
    storage: StorageService = Depends(get_storage),
) -> BatchDeleteResponse:
    deleted = []
    failed: list[BatchDeleteFailure] = []
    for reference in request.files:
        try:
            validate_uuid4(reference.file_id)
            await run_in_threadpool(storage.remove, reference.bucket, reference.file_id)
            deleted.append(reference)
        except (InvalidBucketError, ObjectNotFoundError):
            failed.append(
                BatchDeleteFailure(
                    bucket=reference.bucket,
                    file_id=reference.file_id,
                    code="not_found",
                    message="Object could not be found",
                )
            )
        except (S3Error, urllib3.exceptions.HTTPError, OSError):
            failed.append(
                BatchDeleteFailure(
                    bucket=reference.bucket,
                    file_id=reference.file_id,
                    code="storage_error",
                    message="Object could not be deleted",
                )
            )
    return BatchDeleteResponse(deleted=deleted, failed=failed)
