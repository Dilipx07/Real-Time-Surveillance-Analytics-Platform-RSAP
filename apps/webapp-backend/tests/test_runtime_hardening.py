from types import SimpleNamespace

import pytest

from app.database import create_pool
from app.services.cleanup_service import process_external_cleanup_once
from app.services.file_service import FileService


@pytest.mark.asyncio
async def test_postgres_password_is_passed_without_dsn_interpolation(monkeypatch):
    captured = {}

    async def fake_create_pool(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("app.database.asyncpg.create_pool", fake_create_pool)
    settings = SimpleNamespace(
        postgres_host="db", postgres_port=5432, postgres_db="rsap",
        postgres_user="user", postgres_password="p@ss:/word%strong",
    )
    await create_pool(settings)
    assert captured["password"] == "p@ss:/word%strong"
    assert "dsn" not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize("public_secure,scheme", [(False, "http"), (True, "https")])
async def test_presigned_urls_support_public_http_and_https(
    monkeypatch, public_secure, scheme
):
    class FakeMinio:
        def __init__(self, endpoint, **kwargs):
            self.endpoint = endpoint
            self.secure = kwargs["secure"]

        def presigned_get_object(self, bucket, object_name, **_kwargs):
            return f"{'https' if self.secure else 'http'}://{self.endpoint}/{bucket}/{object_name}"

    monkeypatch.setattr("app.services.file_service.Minio", FakeMinio)
    settings = SimpleNamespace(
        minio_endpoint="minio:9000", minio_public_endpoint="files.example.test",
        minio_access_key="access", minio_secret_key="secret", minio_secure=False,
        minio_public_secure=public_secure, minio_bucket_faces="faces",
        minio_bucket_captures="captures", minio_bucket_documents="documents",
    )
    service = FileService(settings)
    url = await service.get_presigned_url("faces", "example.jpg")
    assert url == f"{scheme}://files.example.test/faces/example.jpg"
    assert "minio:9000" not in url


class FakeConnection:
    def __init__(self):
        self.updates = []

    async def fetch(self, *_):
        return [{"id": 1, "bucket": "faces", "object_name": "old.jpg"}]

    async def execute(self, query, *args):
        self.updates.append((query, args))


class Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_):
        return None


class FailingFileService:
    async def delete_file(self, *_):
        raise OSError("temporary MinIO failure")


@pytest.mark.asyncio
async def test_minio_cleanup_failure_remains_pending_for_retry():
    connection = FakeConnection()
    pool = SimpleNamespace(acquire=lambda: Acquire(connection))
    app = SimpleNamespace(state=SimpleNamespace(db_pool=pool, file_service=FailingFileService()))
    assert await process_external_cleanup_once(app) == 0
    assert any(
        "attempts=attempts+1" in query and "processed_at=NOW()" not in query
        for query, _ in connection.updates
    )
