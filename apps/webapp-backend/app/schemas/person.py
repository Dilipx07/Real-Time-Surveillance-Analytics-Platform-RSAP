from pydantic import Field, field_validator

from app.schemas.common import StrictModel


class PersonUpdate(StrictModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    phone: str | None = Field(default=None, min_length=3, max_length=20)
    aadhaar_last4: str | None = None

    @field_validator("full_name", "phone")
    @classmethod
    def reject_null_required_fields(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("field cannot be null")
        return value

    @field_validator("aadhaar_last4")
    @classmethod
    def validate_aadhaar(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("aadhaar_last4 cannot be null")
        if len(value) != 4 or not value.isdigit():
            raise ValueError("aadhaar_last4 must contain exactly four digits")
        return value
