from xml.etree.ElementTree import tostring

from app.config import Settings
from app.minio_client import StorageService


class FakeHttpClient:
    def __init__(self) -> None:
        self.closed = False

    def clear(self) -> None:
        self.closed = True


class FakeMinioClient:
    def __init__(self) -> None:
        self.existing = {"faces"}
        self.created: list[str] = []
        self.private_policy_repairs: list[str] = []
        self.lifecycle_bucket: str | None = None
        self.lifecycle_xml: str | None = None

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.existing

    def make_bucket(self, bucket: str) -> None:
        self.created.append(bucket)
        self.existing.add(bucket)

    def delete_bucket_policy(self, bucket: str) -> None:
        self.private_policy_repairs.append(bucket)

    def set_bucket_lifecycle(self, bucket: str, lifecycle) -> None:
        self.lifecycle_bucket = bucket
        self.lifecycle_xml = tostring(lifecycle.toxml(None), encoding="unicode")


def make_settings() -> Settings:
    return Settings(
        minio_endpoint="minio:9000",
        minio_access_key="access-key",
        minio_secret_key="secret-key",
        file_server_service_token="a-secure-service-token",
    )


def test_initialize_creates_missing_private_buckets_and_capture_lifecycle() -> None:
    client = FakeMinioClient()
    http_client = FakeHttpClient()
    storage = StorageService(make_settings(), client, http_client)  # type: ignore[arg-type]

    storage.initialize()

    assert client.created == ["captures", "documents"]
    assert client.private_policy_repairs == ["faces", "captures", "documents"]
    assert client.lifecycle_bucket == "captures"
    assert "<Days>90</Days>" in (client.lifecycle_xml or "")
    assert "<ID>expire-captures</ID>" in (client.lifecycle_xml or "")

    storage.close()
    assert http_client.closed
