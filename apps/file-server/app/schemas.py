from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class UploadResponse(BaseModel):
    file_id: UUID
    bucket: str
    object_name: str
    content_type: str
    size: int
    url: str | None = None


class PresignedUrlResponse(BaseModel):
    file_id: UUID
    bucket: str
    url: str
    expires: int


class FileItem(BaseModel):
    file_id: UUID
    object_name: str
    size: int
    etag: str | None = None
    last_modified: datetime | None = None


class FileListResponse(BaseModel):
    bucket: str
    page: int
    page_size: int
    items: list[FileItem]
    has_more: bool


class DeleteResponse(BaseModel):
    file_id: UUID
    bucket: str
    deleted: bool = True


class FileReference(BaseModel):
    bucket: str = Field(min_length=1)
    file_id: UUID

    @field_validator("file_id")
    @classmethod
    def file_id_must_be_uuid4(cls, value: UUID) -> UUID:
        if value.version != 4:
            raise ValueError("file_id must be a UUID v4")
        return value


class BatchDeleteRequest(BaseModel):
    files: list[FileReference] = Field(min_length=1, max_length=100)

    @field_validator("files")
    @classmethod
    def files_must_be_unique(cls, value: list[FileReference]) -> list[FileReference]:
        keys = {(item.bucket, item.file_id) for item in value}
        if len(keys) != len(value):
            raise ValueError("duplicate file references are not allowed")
        return value


class BatchDeleteFailure(BaseModel):
    bucket: str
    file_id: UUID
    code: str
    message: str


class BatchDeleteResponse(BaseModel):
    deleted: list[FileReference]
    failed: list[BatchDeleteFailure]


class HealthResponse(BaseModel):
    status: str
    minio: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorDetail
