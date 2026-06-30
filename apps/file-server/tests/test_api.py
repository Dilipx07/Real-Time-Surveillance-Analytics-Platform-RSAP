import io
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid1, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from minio.error import S3Error

import main
from app.config import Settings, get_settings
from app.minio_client import InvalidBucketError, ObjectNotFoundError, StorageUnavailableError
from app.validation import MAX_UPLOAD_SIZE


TOKEN = "test-service-token-value"
AUTH_HEADERS = {"X-Service-Token": TOKEN}


def jpeg_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (8, 8), color="navy").save(output, format="JPEG")
    return output.getvalue()


class FakeStorage:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(
            minio_bucket_faces="faces",
            minio_bucket_captures="captures",
            minio_bucket_documents="documents",
            buckets=("faces", "captures", "documents"),
            presigned_url_default_expiry_seconds=3600,
            presigned_url_max_expiry_seconds=86400,
        )
        self.objects: dict[tuple[str, str], dict] = {}
        self.initialized = False
        self.closed = False
        self.healthy = True

    def initialize(self) -> None:
        self.initialized = True

    def close(self) -> None:
        self.closed = True

    def check_health(self) -> None:
        if not self.healthy:
            raise StorageUnavailableError("MinIO unavailable")

    def require_bucket(self, bucket: str) -> None:
        if bucket not in self.settings.buckets:
            raise InvalidBucketError(bucket)

    def upload(self, bucket: str, object_name: str, data: bytes, content_type: str) -> None:
        self.require_bucket(bucket)
        self.objects[(bucket, object_name)] = {
            "data": data,
            "content_type": content_type,
            "last_modified": datetime.now(UTC),
        }

    def _resolve(self, bucket: str, file_id: UUID) -> str:
        self.require_bucket(bucket)
        matches = [
            object_name
            for stored_bucket, object_name in self.objects
            if stored_bucket == bucket and UUID(object_name.rsplit("/", 1)[-1].split(".", 1)[0]) == file_id
        ]
        if not matches:
            raise ObjectNotFoundError(str(file_id))
        return matches[0]

    def presigned_url(self, bucket: str, file_id: UUID, expires: int) -> str:
        object_name = self._resolve(bucket, file_id)
        return f"https://minio.test/{bucket}/{object_name}?expires={expires}"

    def remove(self, bucket: str, file_id: UUID) -> None:
        object_name = self._resolve(bucket, file_id)
        del self.objects[(bucket, object_name)]

    def list_files(self, bucket: str, page: int, page_size: int) -> tuple[list[dict], bool]:
        self.require_bucket(bucket)
        all_items = []
        for (stored_bucket, object_name), metadata in sorted(self.objects.items()):
            if stored_bucket != bucket:
                continue
            file_id = UUID(object_name.rsplit("/", 1)[-1].split(".", 1)[0])
            all_items.append(
                {
                    "file_id": file_id,
                    "object_name": object_name,
                    "size": len(metadata["data"]),
                    "etag": "test-etag",
                    "last_modified": metadata["last_modified"],
                }
            )
        start = (page - 1) * page_size
        return all_items[start : start + page_size], len(all_items) > start + page_size


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, storage: FakeStorage):
    monkeypatch.setattr(main, "create_storage", lambda: storage)
    with TestClient(main.app) as test_client:
        yield test_client
    assert storage.initialized
    assert storage.closed


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "minio": "ok"}


def test_health_reports_minio_failure(client: TestClient, storage: FakeStorage) -> None:
    storage.healthy = False
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": {
            "code": "storage_unavailable",
            "message": "Object storage is unavailable",
            "details": None,
        },
    }


def test_protected_route_rejects_missing_and_wrong_tokens(client: TestClient) -> None:
    missing = client.get("/files/faces")
    wrong = client.get("/files/faces", headers={"X-Service-Token": "wrong"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["error"]["code"] == "invalid_service_token"
    assert wrong.json()["error"]["code"] == "invalid_service_token"
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_face_upload_uses_uuid_jpg_and_returns_presigned_url(
    client: TestClient, storage: FakeStorage
) -> None:
    response = client.post(
        "/upload/face",
        headers=AUTH_HEADERS,
        files={"file": ("original-name.jpg", jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 201
    body = response.json()
    file_id = UUID(body["file_id"])
    assert file_id.version == 4
    assert body["object_name"] == f"{file_id}.jpg"
    assert "original-name" not in body["object_name"]
    assert body["url"].startswith("https://minio.test/faces/")
    assert ("faces", body["object_name"]) in storage.objects


def test_capture_upload_uses_utc_date_prefix(client: TestClient) -> None:
    response = client.post(
        "/upload/capture",
        headers=AUTH_HEADERS,
        files={"file": ("capture.jpg", jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["object_name"] == f"{datetime.now(UTC).date()}/{body['file_id']}.jpg"
    assert body["url"] is None


def test_document_upload_uses_validated_category_and_mime_extension(client: TestClient) -> None:
    pdf = b"%PDF-1.7\nminimal\n%%EOF"
    response = client.post(
        "/upload/document/reports_2026",
        headers=AUTH_HEADERS,
        files={"file": ("private-original.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["object_name"] == f"reports_2026/{body['file_id']}.pdf"


@pytest.mark.parametrize(
    ("path", "filename", "content", "content_type", "expected_status"),
    [
        ("/upload/face", "fake.jpg", b"not-a-jpeg", "image/jpeg", 422),
        ("/upload/face", "image.png", b"content", "image/png", 415),
        ("/upload/document/bad.category", "x.txt", b"hello", "text/plain", 422),
    ],
)
def test_upload_validation(
    client: TestClient,
    path: str,
    filename: str,
    content: bytes,
    content_type: str,
    expected_status: int,
) -> None:
    response = client.post(
        path,
        headers=AUTH_HEADERS,
        files={"file": (filename, content, content_type)},
    )
    assert response.status_code == expected_status


def test_upload_rejects_exactly_ten_mebibytes(client: TestClient) -> None:
    response = client.post(
        "/upload/document/text",
        headers=AUTH_HEADERS,
        files={"file": ("large.txt", b"a" * MAX_UPLOAD_SIZE, "text/plain")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_presigned_list_delete_and_missing_file(client: TestClient) -> None:
    upload = client.post(
        "/upload/document/evidence",
        headers=AUTH_HEADERS,
        files={"file": ("note.txt", b"evidence", "text/plain")},
    ).json()
    file_id = upload["file_id"]

    presigned = client.get(
        f"/files/documents/{file_id}/presigned?expires=120",
        headers=AUTH_HEADERS,
    )
    assert presigned.status_code == 200
    assert presigned.json()["expires"] == 120

    listing = client.get("/files/documents?page=1&page_size=1", headers=AUTH_HEADERS)
    assert listing.status_code == 200
    assert listing.json()["items"][0]["file_id"] == file_id

    redirect = client.get(
        f"/files/documents/{file_id}", headers=AUTH_HEADERS, follow_redirects=False
    )
    assert redirect.status_code == 307

    deleted = client.delete(f"/files/documents/{file_id}", headers=AUTH_HEADERS)
    assert deleted.status_code == 200
    assert client.delete(f"/files/documents/{file_id}", headers=AUTH_HEADERS).status_code == 404


def test_batch_delete_reports_per_item_results(client: TestClient) -> None:
    first = client.post(
        "/upload/face",
        headers=AUTH_HEADERS,
        files={"file": ("first.jpg", jpeg_bytes(), "image/jpeg")},
    ).json()["file_id"]
    missing = str(uuid4())
    response = client.post(
        "/files/batch-delete",
        headers=AUTH_HEADERS,
        json={
            "files": [
                {"bucket": "faces", "file_id": first},
                {"bucket": "faces", "file_id": missing},
            ]
        },
    )
    assert response.status_code == 200
    assert len(response.json()["deleted"]) == 1
    assert len(response.json()["failed"]) == 1


def test_batch_delete_all_success(client: TestClient) -> None:
    ids = [
        client.post(
            "/upload/face",
            headers=AUTH_HEADERS,
            files={"file": (f"{index}.jpg", jpeg_bytes(), "image/jpeg")},
        ).json()["file_id"]
        for index in range(2)
    ]
    response = client.post(
        "/files/batch-delete",
        headers=AUTH_HEADERS,
        json={"files": [{"bucket": "faces", "file_id": file_id} for file_id in ids]},
    )
    assert response.status_code == 200
    assert len(response.json()["deleted"]) == 2
    assert response.json()["failed"] == []


def test_non_v4_file_id_is_rejected(client: TestClient) -> None:
    response = client.get(f"/files/faces/{uuid1()}/presigned", headers=AUTH_HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_every_face_url_path_obeys_configured_expiry(
    client: TestClient,
) -> None:
    constrained = Settings(
        minio_internal_endpoint="minio:9000",
        minio_public_endpoint="s3.test.local",
        minio_access_key="test-access-key",
        minio_secret_key="test-secret-key-value",
        file_server_service_token=TOKEN,
        presigned_url_max_expiry_seconds=60,
    )
    main.app.dependency_overrides[get_settings] = lambda: constrained
    try:
        uploaded = client.post(
            "/upload/face",
            headers=AUTH_HEADERS,
            files={"file": ("face.jpg", jpeg_bytes(), "image/jpeg")},
        )
        file_id = uploaded.json()["file_id"]
        redirect = client.get(
            f"/files/faces/{file_id}", headers=AUTH_HEADERS, follow_redirects=False
        )
        explicit = client.get(
            f"/files/faces/{file_id}/presigned?expires=3600", headers=AUTH_HEADERS
        )
    finally:
        main.app.dependency_overrides.pop(get_settings, None)
    assert "expires=60" in uploaded.json()["url"]
    assert "expires=60" in redirect.headers["location"]
    assert explicit.json()["expires"] == 60
    assert "expires=60" in explicit.json()["url"]


def test_lifespan_closes_storage_when_initialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = FakeStorage()

    def fail_initialization() -> None:
        raise ConnectionError("temporary failure")

    storage.initialize = fail_initialization  # type: ignore[method-assign]
    monkeypatch.setattr(main, "create_storage", lambda: storage)

    async def enter_lifespan() -> None:
        async with main.lifespan(main.app):
            pass

    with pytest.raises(ConnectionError):
        asyncio.run(enter_lifespan())
    assert storage.closed


def test_unexpected_health_error_is_not_misclassified(
    client: TestClient,
    storage: FakeStorage,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail() -> None:
        raise RuntimeError("sensitive diagnostic must not be logged")

    storage.check_health = fail  # type: ignore[method-assign]
    response = client.get("/health")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "health_check_failed"
    assert "RuntimeError" in caplog.text
    assert "sensitive diagnostic" not in caplog.text


def test_error_responses_share_one_schema(client: TestClient) -> None:
    responses = [
        client.get("/files/faces"),
        client.get(f"/files/faces/{uuid4()}", headers=AUTH_HEADERS),
        client.post(
            "/upload/face",
            headers=AUTH_HEADERS,
            files={"file": ("wrong.png", b"png", "image/png")},
        ),
        client.get("/files/faces/not-a-uuid", headers=AUTH_HEADERS),
    ]
    assert [response.status_code for response in responses] == [401, 404, 415, 422]
    for response in responses:
        assert response.json().keys() == {"success", "error"}
        assert response.json()["success"] is False
        assert response.json()["error"].keys() == {"code", "message", "details"}


def test_storage_error_is_sanitized_and_standardized(
    client: TestClient, storage: FakeStorage
) -> None:
    def fail_listing(_bucket: str, _page: int, _page_size: int):
        raise S3Error(
            "InternalError",
            "raw storage message",
            "/private/resource",
            "request-id",
            "host-id",
            None,  # type: ignore[arg-type]
        )

    storage.list_files = fail_listing  # type: ignore[method-assign]
    response = client.get("/files/faces", headers=AUTH_HEADERS)
    assert response.status_code == 502
    assert response.json() == {
        "success": False,
        "error": {
            "code": "storage_error",
            "message": "Object storage request failed",
            "details": None,
        },
    }
    assert "raw storage message" not in response.text


def test_batch_delete_continues_after_storage_errors(
    client: TestClient, storage: FakeStorage
) -> None:
    uploads = [
        client.post(
            "/upload/face",
            headers=AUTH_HEADERS,
            files={"file": (f"{index}.jpg", jpeg_bytes(), "image/jpeg")},
        ).json()
        for index in range(3)
    ]
    failed_id = UUID(uploads[1]["file_id"])
    original_remove = storage.remove

    def partly_failing_remove(bucket: str, file_id: UUID) -> None:
        if file_id == failed_id:
            raise OSError("raw storage detail")
        original_remove(bucket, file_id)

    storage.remove = partly_failing_remove  # type: ignore[method-assign]
    response = client.post(
        "/files/batch-delete",
        headers=AUTH_HEADERS,
        json={
            "files": [
                {"bucket": "faces", "file_id": upload["file_id"]}
                for upload in uploads
            ]
        },
    )
    assert response.status_code == 200
    assert len(response.json()["deleted"]) == 2
    assert response.json()["failed"] == [
        {
            "bucket": "faces",
            "file_id": str(failed_id),
            "code": "storage_error",
            "message": "Object could not be deleted",
        }
    ]
    assert "raw storage detail" not in response.text


def test_batch_delete_all_failure_and_duplicate_validation(
    client: TestClient, storage: FakeStorage
) -> None:
    storage.remove = lambda _bucket, _file_id: (_ for _ in ()).throw(OSError())  # type: ignore[method-assign]
    ids = [str(uuid4()), str(uuid4())]
    failed = client.post(
        "/files/batch-delete",
        headers=AUTH_HEADERS,
        json={"files": [{"bucket": "faces", "file_id": file_id} for file_id in ids]},
    )
    duplicate = client.post(
        "/files/batch-delete",
        headers=AUTH_HEADERS,
        json={"files": [{"bucket": "faces", "file_id": ids[0]}] * 2},
    )
    assert failed.status_code == 200
    assert failed.json()["deleted"] == []
    assert len(failed.json()["failed"]) == 2
    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["code"] == "validation_error"


def test_openapi_documents_shared_error_schema() -> None:
    schema = main.app.openapi()
    operation = schema["paths"]["/files/{bucket}"]["get"]
    assert operation["responses"]["401"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ErrorResponse")
