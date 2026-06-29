from pydantic import Field

from app.schemas.common import EmailString, StrictModel


class LoginRequest(StrictModel):
    email: EmailString
    password: str = Field(min_length=8, max_length=128)
    device_fingerprint: str | None = Field(default=None, max_length=512)


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=20)
