import pytest
from pydantic import ValidationError

from app.config import Settings


BASE_SETTINGS = {
    "minio_endpoint": "minio:9000",
    "minio_access_key": "access-key",
    "minio_secret_key": "secret-key",
    "file_server_service_token": "a-secure-test-token",
}


def test_required_settings_validate() -> None:
    settings = Settings(**BASE_SETTINGS)
    assert settings.buckets == ("faces", "captures", "documents")
    assert settings.capture_retention_days == 90


def test_short_service_token_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**(BASE_SETTINGS | {"file_server_service_token": "short"}))


def test_duplicate_bucket_names_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(**(BASE_SETTINGS | {"minio_bucket_faces": "documents"}))


@pytest.mark.parametrize("bucket", ["invalid..bucket", "192.168.1.1", "-invalid"])
def test_invalid_bucket_names_are_rejected(bucket: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**(BASE_SETTINGS | {"minio_bucket_faces": bucket}))
