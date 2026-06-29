from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    postgres_host: str
    postgres_port: int = 5432
    postgres_db: str
    postgres_user: str
    postgres_password: str
    redis_url: str

    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket_faces: str = "faces"
    minio_bucket_captures: str = "captures"
    minio_bucket_documents: str = "documents"
    minio_secure: bool = False

    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 15
    jwt_refresh_expire_days: int = 7
    aes_encryption_key: str = Field(min_length=16)
    license_signing_secret: str = Field(min_length=16)
    app_env: str = "development"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000,tauri://localhost"

    @field_validator("jwt_algorithm")
    @classmethod
    def validate_algorithm(cls, value: str) -> str:
        if value not in {"HS256", "HS384", "HS512"}:
            raise ValueError("JWT_ALGORITHM must be an HMAC SHA-2 algorithm")
        return value

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
