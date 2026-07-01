"""Strict desktop-daemon configuration."""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    return Path.home() / ".rsap"


def decode_key(value: SecretStr, name: str) -> bytes:
    """Decode an exact 256-bit URL-safe base64 secret."""
    raw = value.get_secret_value().encode("ascii")
    try:
        decoded = base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))
    except (ValueError, UnicodeEncodeError) as exc:
        raise ValueError(f"{name} must be URL-safe base64") from exc
    if len(decoded) != 32:
        raise ValueError(f"{name} must decode to exactly 32 bytes")
    return decoded


class Settings(BaseSettings):
    """Runtime settings with no production secret defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RSAP_",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["development", "test", "production"] = "production"
    host: str = "127.0.0.1"
    port: int = Field(default=8001, ge=1, le=65535)
    data_dir: Path = Field(default_factory=_default_data_dir)
    database_path: Path | None = None
    database_driver: Literal["sqlcipher", "sqlite-test"] = "sqlcipher"
    database_key: SecretStr
    field_encryption_key: SecretStr

    central_api_url: str
    request_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    connect_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    retry_attempts: int = Field(default=3, ge=1, le=5)
    retry_base_delay_seconds: float = Field(default=0.25, ge=0, le=5)
    queue_lease_seconds: int = Field(default=60, ge=5, le=600)
    cors_origins: tuple[str, ...] = (
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    )

    @field_validator("host")
    @classmethod
    def localhost_only(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("desktop backend must bind only to loopback")
        return value

    @field_validator("central_api_url")
    @classmethod
    def validate_central_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("central_api_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("central_api_url must not contain credentials, query, or fragment")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        decode_key(self.database_key, "database_key")
        decode_key(self.field_encryption_key, "field_encryption_key")
        if self.database_driver == "sqlite-test" and self.environment != "test":
            raise ValueError("sqlite-test driver is permitted only in the test environment")
        if self.environment == "production" and not self.central_api_url.startswith("https://"):
            raise ValueError("production central_api_url must use HTTPS")
        if self.database_path is None:
            self.database_path = self.data_dir / "local.db"
        elif not self.database_path.is_absolute():
            raise ValueError("database_path must be absolute")
        return self

    @property
    def database_key_bytes(self) -> bytes:
        return decode_key(self.database_key, "database_key")

    @property
    def field_encryption_key_bytes(self) -> bytes:
        return decode_key(self.field_encryption_key, "field_encryption_key")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

