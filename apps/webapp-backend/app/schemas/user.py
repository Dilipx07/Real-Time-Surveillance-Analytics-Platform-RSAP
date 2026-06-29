from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import EmailString, StrictModel


Role = Literal["staff", "va_user"]


class UserCreate(StrictModel):
    email: EmailString
    password: str = Field(min_length=8, max_length=128)
    role: Role
    phone: str | None = Field(default=None, max_length=20)
    whatsapp_number: str | None = Field(default=None, max_length=20)


class UserUpdate(StrictModel):
    email: EmailString | None = None
    phone: str | None = Field(default=None, max_length=20)
    whatsapp_number: str | None = Field(default=None, max_length=20)
    role: Role | None = None

    @field_validator("email", "role")
    @classmethod
    def reject_null_required_fields(cls, value):
        if value is None:
            raise ValueError("field cannot be null")
        return value


class PermissionCreate(StrictModel):
    resource: str = Field(min_length=1, max_length=100)
    actions: list[str] = Field(min_length=1)
    constraints: dict = Field(default_factory=dict)


class UserIdRequest(StrictModel):
    user_id: UUID
