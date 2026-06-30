from uuid import uuid4

import pytest

from app.config import Settings
from app.minio_client import StorageService
from app.presigned import clamp_presigned_expiry


def settings(**overrides) -> Settings:
    values = {
        "minio_internal_endpoint": "minio:9000",
        "minio_public_endpoint": "public.example.test:9443",
        "minio_access_key": "test-access-key",
        "minio_secret_key": "test-secret-key-value",
        "file_server_service_token": "test-service-token-value",
    }
    return Settings(**(values | overrides))


def test_expiry_is_clamped_for_default_and_explicit_values() -> None:
    configured = settings(
        presigned_url_default_expiry_seconds=3600,
        presigned_url_max_expiry_seconds=60,
    )
    assert clamp_presigned_expiry(configured) == 60
    assert clamp_presigned_expiry(configured, 3600) == 60
    assert clamp_presigned_expiry(configured, 60) == 60
    with pytest.raises(ValueError):
        clamp_presigned_expiry(configured, 59)


def test_signing_uses_public_endpoint_without_rewriting() -> None:
    configured = settings(minio_public_secure=True)
    storage = StorageService.create(configured)
    storage.resolve_object_name = lambda _bucket, _file_id: f"{_file_id}.jpg"  # type: ignore[method-assign]
    try:
        url = storage.presigned_url("faces", uuid4(), 60)
    finally:
        storage.close()
    assert url.startswith("https://public.example.test:9443/faces/")
    assert "minio:9000" not in url
