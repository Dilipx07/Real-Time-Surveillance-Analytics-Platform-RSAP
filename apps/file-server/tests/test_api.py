import io
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid1, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
from app.minio_client import InvalidBucketError, ObjectNotFoundError
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
            raise ConnectionError("MinIO unavailable")

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
    assert response.json() == {"status": "degraded", "minio": "unavailable"}


def test_protected_route_rejects_missing_and_wrong_tokens(client: TestClient) -> None:
    assert client.get("/files/faces").status_code == 401
    assert client.get("/files/faces", headers={"X-Service-Token": "wrong"}).status_code == 401
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


def test_non_v4_file_id_is_rejected(client: TestClient) -> None:
    response = client.get(f"/files/faces/{uuid1()}/presigned", headers=AUTH_HEADERS)
    assert response.status_code == 422
