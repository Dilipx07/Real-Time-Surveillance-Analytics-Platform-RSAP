import asyncio
from datetime import timedelta
from io import BytesIO
from uuid import uuid4

from minio import Minio

from app.config import Settings


class FileService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    async def create_buckets_if_not_exist(self) -> None:
        for bucket in (
            self.settings.minio_bucket_faces,
            self.settings.minio_bucket_captures,
            self.settings.minio_bucket_documents,
        ):
            exists = await asyncio.to_thread(self.client.bucket_exists, bucket)
            if not exists:
                await asyncio.to_thread(self.client.make_bucket, bucket)

    async def upload_file(self, bucket: str, data: bytes, content_type: str, prefix: str = "") -> str:
        extension = {"image/jpeg": ".jpg", "image/png": ".png"}.get(content_type, "")
        object_name = f"{prefix.rstrip('/') + '/' if prefix else ''}{uuid4()}{extension}"
        await asyncio.to_thread(
            self.client.put_object,
            bucket,
            object_name,
            BytesIO(data),
            len(data),
            content_type=content_type,
        )
        return object_name

    async def get_presigned_url(self, bucket: str, object_name: str, expires: int = 3600) -> str:
        return await asyncio.to_thread(
            self.client.presigned_get_object, bucket, object_name, expires=timedelta(seconds=expires)
        )

    async def delete_file(self, bucket: str, object_name: str) -> None:
        await asyncio.to_thread(self.client.remove_object, bucket, object_name)

    async def health(self) -> bool:
        try:
            await asyncio.to_thread(lambda: list(self.client.list_buckets()))
            return True
        except Exception:
            return False
