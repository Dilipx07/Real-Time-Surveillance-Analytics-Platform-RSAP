import io
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from minio import Minio
from minio.error import S3Error
from PIL import Image

import main
from app.config import Settings, get_settings
from app.minio_client import StorageService


pytestmark = pytest.mark.integration


@dataclass(frozen=True)
class MinioTestServer:
    endpoint: str
    access_key: str
    secret_key: str
    client: Minio


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
    )


@contextmanager
def disposable_minio():
    if os.getenv("RSAP_RUN_MINIO_INTEGRATION") != "1":
        pytest.skip("set RSAP_RUN_MINIO_INTEGRATION=1 to run Docker integration tests")

    suffix = uuid4().hex[:12]
    container = f"rsap-file-server-test-{suffix}"
    access_key = f"rsaptest{suffix}"
    secret_key = f"rsap-test-secret-{suffix}-value"
    try:
        docker(
            "run",
            "--detach",
            "--publish-all",
            "--name",
            container,
            "--env",
            f"MINIO_ROOT_USER={access_key}",
            "--env",
            f"MINIO_ROOT_PASSWORD={secret_key}",
            "minio/minio:latest",
            "server",
            "/data",
        )
        port_output = docker("port", container, "9000/tcp").stdout
        match = re.search(r"(?:0\.0\.0\.0|127\.0\.0\.1):(\d+)", port_output)
        if match is None:
            raise RuntimeError(f"Could not determine MinIO test port: {port_output!r}")
        endpoint = f"127.0.0.1:{match.group(1)}"
        client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=False,
            region="us-east-1",
        )
        deadline = time.monotonic() + 45
        while True:
            try:
                client.list_buckets()
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
        yield MinioTestServer(endpoint, access_key, secret_key, client)
    finally:
        docker("rm", "--force", container, check=False)


@pytest.fixture(scope="module")
def minio_server():
    with disposable_minio() as server:
        yield server


@pytest.fixture
def real_client(monkeypatch: pytest.MonkeyPatch, minio_server: MinioTestServer):
    settings = Settings(
        minio_internal_endpoint=minio_server.endpoint.replace("127.0.0.1", "localhost"),
        minio_public_endpoint=minio_server.endpoint,
        minio_access_key=minio_server.access_key,
        minio_secret_key=minio_server.secret_key,
        minio_internal_secure=False,
        minio_public_secure=False,
        file_server_service_token="integration-service-token-value",
    )
    monkeypatch.setattr(main, "create_storage", lambda: StorageService.create(settings))
    main.app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(main.app) as client:
            yield client, minio_server, settings
    finally:
        main.app.dependency_overrides.pop(get_settings, None)


def jpeg_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "navy").save(output, format="JPEG")
    return output.getvalue()


def test_real_minio_lifecycle_prefixes_and_external_url(real_client) -> None:
    client, server, _settings = real_client
    headers = {"X-Service-Token": "integration-service-token-value"}

    assert {bucket.name for bucket in server.client.list_buckets()} == {
        "faces",
        "captures",
        "documents",
    }
    for bucket in ("faces", "captures", "documents"):
        with pytest.raises(S3Error) as error:
            server.client.get_bucket_policy(bucket)
        assert error.value.code in {"NoSuchBucketPolicy", "NoSuchPolicy"}
    lifecycle = server.client.get_bucket_lifecycle("captures")
    assert lifecycle is not None
    assert lifecycle.rules[0].expiration.days == 90
    assert server.client.get_bucket_lifecycle("faces") is None
    assert server.client.get_bucket_lifecycle("documents") is None

    face = client.post(
        "/upload/face",
        headers=headers,
        files={"file": ("ignored.jpg", jpeg_bytes(), "image/jpeg")},
    ).json()
    capture = client.post(
        "/upload/capture",
        headers=headers,
        files={"file": ("ignored.jpg", jpeg_bytes(), "image/jpeg")},
    ).json()
    document = client.post(
        "/upload/document/reports",
        headers=headers,
        files={"file": ("ignored.txt", b"integration", "text/plain")},
    ).json()

    assert face["object_name"] == f'{face["file_id"]}.jpg'
    assert re.fullmatch(rf"\d{{4}}-\d{{2}}-\d{{2}}/{capture['file_id']}\.jpg", capture["object_name"])
    assert document["object_name"] == f'reports/{document["file_id"]}.txt'
    assert "minio:9000" not in face["url"]
    external = httpx.get(face["url"], timeout=10)
    assert external.status_code == 200
    assert external.content == jpeg_bytes()

    for bucket, item in (("captures", capture), ("documents", document)):
        presigned = client.get(
            f'/files/{bucket}/{item["file_id"]}/presigned', headers=headers
        )
        assert presigned.status_code == 200
        assert server.endpoint in presigned.json()["url"]
        deleted = client.delete(f'/files/{bucket}/{item["file_id"]}', headers=headers)
        assert deleted.status_code == 200
        with pytest.raises(S3Error):
            server.client.stat_object(bucket, item["object_name"])


def test_real_minio_continuation_pagination_and_batch_delete(real_client) -> None:
    client, server, settings = real_client
    headers = {"X-Service-Token": "integration-service-token-value"}
    object_names = [f"bulk/{uuid4()}.txt" for _ in range(1005)]

    def put(name: str) -> None:
        server.client.put_object(
            settings.minio_bucket_documents,
            name,
            io.BytesIO(b"x"),
            1,
            content_type="text/plain",
        )

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(put, object_names))

    page_11 = client.get(
        "/files/documents?page=11&page_size=100", headers=headers
    )
    assert page_11.status_code == 200
    assert len(page_11.json()["items"]) >= 5
    assert page_11.json()["has_more"] is False

    ids = [item["file_id"] for item in page_11.json()["items"][:2]]
    batch = client.post(
        "/files/batch-delete",
        headers=headers,
        json={
            "files": [
                {"bucket": "documents", "file_id": file_id} for file_id in ids
            ]
            + [{"bucket": "documents", "file_id": str(uuid4())}]
        },
    )
    assert batch.status_code == 200
    assert len(batch.json()["deleted"]) == 2
    assert len(batch.json()["failed"]) == 1
