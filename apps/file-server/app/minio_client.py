import io
from datetime import timedelta
from pathlib import PurePosixPath
from uuid import UUID

import urllib3
from minio import Minio
from minio.error import S3Error
from minio.lifecycleconfig import Expiration, Filter, LifecycleConfig, Rule

from app.config import Settings


class ObjectNotFoundError(Exception):
    """Raised when no stored object corresponds to a file UUID."""


class InvalidBucketError(Exception):
    """Raised when a request names a bucket outside this service's ownership."""


class StorageService:
    """Synchronous MinIO operations, called from FastAPI worker threads."""

    def __init__(self, settings: Settings, client: Minio, http_client: urllib3.PoolManager) -> None:
        self.settings = settings
        self.client = client
        self._http_client = http_client

    @classmethod
    def create(cls, settings: Settings) -> "StorageService":
        http_client = urllib3.PoolManager(
            timeout=urllib3.Timeout(connect=5.0, read=30.0),
            retries=urllib3.Retry(
                total=3,
                backoff_factor=0.2,
                status_forcelist=[500, 502, 503, 504],
            ),
        )
        client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            http_client=http_client,
        )
        return cls(settings=settings, client=client, http_client=http_client)

    def initialize(self) -> None:
        for bucket in self.settings.buckets:
            self._ensure_bucket(bucket)
            self._ensure_private(bucket)
        self._set_capture_lifecycle()

    def close(self) -> None:
        self._http_client.clear()

    def check_health(self) -> None:
        self.client.list_buckets()

    def upload(
        self,
        bucket: str,
        object_name: str,
        data: bytes,
        content_type: str,
    ) -> None:
        self.require_bucket(bucket)
        self.client.put_object(
            bucket_name=bucket,
            object_name=object_name,
            data=io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def presigned_url(self, bucket: str, file_id: UUID, expires: int) -> str:
        object_name = self.resolve_object_name(bucket, file_id)
        return self.client.presigned_get_object(
            bucket_name=bucket,
            object_name=object_name,
            expires=timedelta(seconds=expires),
        )

    def remove(self, bucket: str, file_id: UUID) -> None:
        object_name = self.resolve_object_name(bucket, file_id)
        self.client.remove_object(bucket, object_name)

    def list_files(self, bucket: str, page: int, page_size: int) -> tuple[list[dict], bool]:
        self.require_bucket(bucket)
        offset = (page - 1) * page_size
        accepted = 0
        items: list[dict] = []

        for stored_object in self.client.list_objects(bucket, recursive=True):
            file_id = self._file_id_from_object_name(stored_object.object_name)
            if file_id is None:
                continue
            if accepted < offset:
                accepted += 1
                continue
            if len(items) == page_size:
                return items, True
            items.append(
                {
                    "file_id": file_id,
                    "object_name": stored_object.object_name,
                    "size": stored_object.size or 0,
                    "etag": stored_object.etag,
                    "last_modified": stored_object.last_modified,
                }
            )
            accepted += 1
        return items, False

    def resolve_object_name(self, bucket: str, file_id: UUID) -> str:
        self.require_bucket(bucket)
        direct_name = f"{file_id}.jpg"
        if bucket == self.settings.minio_bucket_faces:
            try:
                self.client.stat_object(bucket, direct_name)
            except S3Error as exc:
                if exc.code in {"NoSuchKey", "NoSuchObject", "NotFound"}:
                    raise ObjectNotFoundError(str(file_id)) from exc
                raise
            return direct_name

        matches = [
            item.object_name
            for item in self.client.list_objects(bucket, recursive=True)
            if self._file_id_from_object_name(item.object_name) == file_id
        ]
        if not matches:
            raise ObjectNotFoundError(str(file_id))
        if len(matches) > 1:
            raise RuntimeError(f"Duplicate object UUID detected in bucket {bucket}: {file_id}")
        return matches[0]

    def require_bucket(self, bucket: str) -> str:
        if bucket not in self.settings.buckets:
            raise InvalidBucketError(bucket)
        return bucket

    def _ensure_private(self, bucket: str) -> None:
        # A MinIO bucket with no anonymous policy is private. Remove a stale policy
        # so service restarts also repair an accidentally public bucket.
        try:
            self.client.delete_bucket_policy(bucket)
        except S3Error as exc:
            if exc.code not in {"NoSuchBucketPolicy", "NoSuchPolicy", "NoSuchBucket"}:
                raise

    def _ensure_bucket(self, bucket: str) -> None:
        if self.client.bucket_exists(bucket):
            return
        try:
            self.client.make_bucket(bucket)
        except S3Error as exc:
            # Multiple Uvicorn workers initialize concurrently. Another worker
            # may create the bucket between bucket_exists() and make_bucket().
            if exc.code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise
            if not self.client.bucket_exists(bucket):
                raise

    def _set_capture_lifecycle(self) -> None:
        lifecycle = LifecycleConfig(
            [
                Rule(
                    status="Enabled",
                    rule_filter=Filter(prefix=""),
                    rule_id="expire-captures",
                    expiration=Expiration(days=self.settings.capture_retention_days),
                )
            ]
        )
        self.client.set_bucket_lifecycle(self.settings.minio_bucket_captures, lifecycle)

    @staticmethod
    def _file_id_from_object_name(object_name: str) -> UUID | None:
        try:
            file_id = UUID(PurePosixPath(object_name).stem)
        except ValueError:
            return None
        return file_id if file_id.version == 4 else None
