import pytest
from pydantic import ValidationError

from app.config import Settings


BASE_SETTINGS = {
    "minio_internal_endpoint": "minio:9000",
    "minio_public_endpoint": "s3.test.local",
    "minio_access_key": "access-key",
    "minio_secret_key": "secret-key-value",
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minio_access_key", "minioadmin"),
        ("minio_secret_key", "change_me_minio_secret"),
        ("file_server_service_token", "placeholder_service_token"),
        ("file_server_service_token", "<required-service-token>"),
    ],
)
def test_placeholder_credentials_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**(BASE_SETTINGS | {field: value}))


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://s3.example.test",
        "host/path",
        "user@host:9000",
        "host:99999",
        "bad host:9000",
        "-bad.example:9000",
    ],
)
def test_invalid_endpoints_are_rejected(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        Settings(**(BASE_SETTINGS | {"minio_public_endpoint": endpoint}))


def test_missing_required_credentials_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "MINIO_INTERNAL_ENDPOINT",
        "MINIO_PUBLIC_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "FILE_SERVER_SERVICE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "public_endpoint",
    [
        "localhost:9000",
        "localhost.localdomain:9000",
        "127.0.0.1:9000",
        "127.1.2.3:9000",
        "[::1]:9000",
        "[::]:9000",
        "[fe80::1]:9000",
        "0.0.0.0:9000",
        "169.254.10.20:9000",
        "minio:9000",
        "redis:9000",
        "postgres:5432",
        "file-server:8002",
        "webapp-backend:8000",
        "webapp-frontend:3000",
        "caddy:443",
        "internalhost:9000",
        "10.1.2.3:9000",
    ],
)
def test_production_rejects_local_or_internal_public_endpoints(
    public_endpoint: str,
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            **(
                BASE_SETTINGS
                | {"app_env": "production", "minio_public_endpoint": public_endpoint}
            )
        )


@pytest.mark.parametrize(
    "public_endpoint",
    [
        "s3.rsap.example.com",
        "s3.rsap.example.com:443",
        "203.0.113.20:9000",
        "8.8.8.8:9000",
        "[2001:4860:4860::8888]:9000",
    ],
)
def test_production_accepts_qualified_dns_and_public_ips(public_endpoint: str) -> None:
    settings = Settings(
        **(
            BASE_SETTINGS
            | {"app_env": "production", "minio_public_endpoint": public_endpoint}
        )
    )
    assert settings.minio_public_endpoint == public_endpoint


@pytest.mark.parametrize("environment", ["development", "test"])
@pytest.mark.parametrize("public_endpoint", ["localhost:9000", "127.0.0.1:9000"])
def test_nonproduction_explicitly_allows_loopback(
    environment: str, public_endpoint: str
) -> None:
    settings = Settings(
        **(
            BASE_SETTINGS
            | {"app_env": environment, "minio_public_endpoint": public_endpoint}
        )
    )
    assert settings.minio_public_endpoint == public_endpoint


@pytest.mark.parametrize(
    "public_endpoint",
    ["minio:9000", "redis:9000", "internalhost:9000", "host/path", "user@host:9000"],
)
def test_development_still_rejects_malformed_or_docker_endpoints(
    public_endpoint: str,
) -> None:
    with pytest.raises(ValidationError):
        Settings(
            **(
                BASE_SETTINGS
                | {"app_env": "development", "minio_public_endpoint": public_endpoint}
            )
        )
