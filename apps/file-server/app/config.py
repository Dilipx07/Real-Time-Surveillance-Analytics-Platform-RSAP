import ipaddress
from functools import lru_cache
from typing import Self

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

    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket_faces: str = "faces"
    minio_bucket_captures: str = "captures"
    minio_bucket_documents: str = "documents"
    minio_secure: bool = False
    file_server_service_token: str = Field(min_length=16)
    capture_retention_days: int = Field(default=90, ge=1, le=3650)
    presigned_url_max_expiry_seconds: int = Field(default=86400, ge=60, le=604800)

    @field_validator(
        "minio_endpoint",
        "minio_access_key",
        "minio_secret_key",
        "file_server_service_token",
    )
    @classmethod
    def value_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
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
