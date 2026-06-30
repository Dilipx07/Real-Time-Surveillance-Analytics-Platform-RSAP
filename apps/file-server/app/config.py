import ipaddress
from functools import lru_cache
from typing import Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded exclusively from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    minio_internal_endpoint: str
    minio_public_endpoint: str
    minio_access_key: str = Field(min_length=3)
    minio_secret_key: str = Field(min_length=16)
    minio_bucket_faces: str = "faces"
    minio_bucket_captures: str = "captures"
    minio_bucket_documents: str = "documents"
    minio_internal_secure: bool = False
    minio_public_secure: bool = True
    minio_region: str = Field(default="us-east-1", min_length=1, max_length=64)
    file_server_service_token: str = Field(min_length=16)
    capture_retention_days: int = Field(default=90, ge=1, le=3650)
    presigned_url_default_expiry_seconds: int = Field(default=3600, ge=60, le=604800)
    presigned_url_max_expiry_seconds: int = Field(default=86400, ge=60, le=604800)

    @field_validator(
        "minio_access_key",
        "minio_secret_key",
        "file_server_service_token",
    )
    @classmethod
    def value_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        normalized = value.casefold()
        if (
            normalized == "minioadmin"
            or normalized.startswith(
                ("change_me", "changeme", "placeholder", "example", "your_")
            )
            or (value.startswith("<") and value.endswith(">"))
        ):
            raise ValueError("placeholder credentials are not allowed")
        return value

    @field_validator("minio_internal_endpoint", "minio_public_endpoint")
    @classmethod
    def validate_minio_endpoint(cls, value: str) -> str:
        value = value.strip()
        if not value or "://" in value:
            raise ValueError("endpoint must be a host or host:port without a URL scheme")
        parsed = urlsplit(f"//{value}")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("endpoint contains an invalid port") from exc
        if (
            not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("endpoint must contain only a host and optional port")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("endpoint port must be between 1 and 65535")
        return value

    @field_validator(
        "minio_bucket_faces",
        "minio_bucket_captures",
        "minio_bucket_documents",
    )
    @classmethod
    def validate_bucket_name(cls, value: str) -> str:
        value = value.strip().lower()
        if not 3 <= len(value) <= 63:
            raise ValueError("bucket names must contain between 3 and 63 characters")
        if not value[0].isalnum() or not value[-1].isalnum():
            raise ValueError("bucket names must start and end with a letter or digit")
        if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for character in value):
            raise ValueError("bucket names may contain only lowercase letters, digits, dots, and hyphens")
        if ".." in value or ".-" in value or "-." in value:
            raise ValueError("bucket names contain an invalid dot or hyphen sequence")
        try:
            ipaddress.ip_address(value)
        except ValueError:
            pass
        else:
            raise ValueError("bucket names must not be formatted as IP addresses")
        return value

    @model_validator(mode="after")
    def buckets_must_be_distinct(self) -> Self:
        buckets = {
            self.minio_bucket_faces,
            self.minio_bucket_captures,
            self.minio_bucket_documents,
        }
        if len(buckets) != 3:
            raise ValueError("MinIO bucket names must be distinct")
        if self.presigned_url_default_expiry_seconds < 60:
            raise ValueError("default presigned expiry must be at least 60 seconds")
        return self

    @property
    def buckets(self) -> tuple[str, str, str]:
        return (
            self.minio_bucket_faces,
            self.minio_bucket_captures,
            self.minio_bucket_documents,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
