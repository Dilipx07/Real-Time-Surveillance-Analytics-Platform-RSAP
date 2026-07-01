import io
import re
import zipfile
from collections.abc import Callable
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError


MAX_UPLOAD_SIZE = 10 * 1024 * 1024
READ_CHUNK_SIZE = 1024 * 1024

IMAGE_CONTENT_TYPES = {"image/jpeg": ".jpg"}
DOCUMENT_CONTENT_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
}
CATEGORY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


async def read_validated_upload(upload: UploadFile, allowed_types: set[str]) -> tuple[bytes, str]:
    content_type = (upload.content_type or "").lower().strip()
    if content_type not in allowed_types:
        allowed = ", ".join(sorted(allowed_types))
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported MIME type. Allowed types: {allowed}",
        )

    chunks: list[bytes] = []
    size = 0
    try:
        while chunk := await upload.read(READ_CHUNK_SIZE):
            size += len(chunk)
            if size >= MAX_UPLOAD_SIZE:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Upload size must be below 10 MB",
                )
            chunks.append(chunk)
    finally:
        await upload.close()

    if size == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    data = b"".join(chunks)
    _validate_content(data, content_type)
    return data, content_type


def validate_category(category: str) -> str:
    normalized = category.strip().lower()
    if not CATEGORY_PATTERN.fullmatch(normalized):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Category must contain only lowercase letters, digits, underscores, or hyphens",
        )
    return normalized


def validate_uuid4(value: UUID) -> UUID:
    if value.version != 4:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="file_id must be a UUID v4",
        )
    return value


def _validate_content(data: bytes, content_type: str) -> None:
    validators: dict[str, Callable[[bytes], None]] = {
        "image/jpeg": lambda content: _validate_image(content, "JPEG"),
        "image/png": lambda content: _validate_image(content, "PNG"),
        "application/pdf": _validate_pdf,
        "text/plain": _validate_utf8,
        "text/csv": _validate_utf8,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
            lambda content: _validate_openxml(content, "word/")
        ),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
            lambda content: _validate_openxml(content, "xl/")
        ),
    }
    try:
        validators[content_type](data)
    except (OSError, UnicodeDecodeError, UnidentifiedImageError, zipfile.BadZipFile, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File content does not match its declared MIME type",
        ) from exc


def _validate_image(data: bytes, expected_format: str) -> None:
    with Image.open(io.BytesIO(data)) as image:
        if image.format != expected_format:
            raise ValueError("unexpected image format")
        image.verify()


def _validate_pdf(data: bytes) -> None:
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-1024:]:
        raise ValueError("invalid PDF signature")


def _validate_utf8(data: bytes) -> None:
    data.decode("utf-8")


def _validate_openxml(data: bytes, required_prefix: str) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if "[Content_Types].xml" not in names or not any(
            name.startswith(required_prefix) for name in names
        ):
            raise ValueError("invalid Open XML document")
