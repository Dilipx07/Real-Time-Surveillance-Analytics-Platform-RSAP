from datetime import datetime
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.common import UTCModel


class LicenseCreate(UTCModel):
    user_id: UUID
    valid_from: datetime
    valid_until: datetime
    max_cameras: int = Field(default=8, ge=1, le=64)
    features: dict = Field(default_factory=dict)
    analytics_modules: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_period(self):
        if self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be after valid_from")
        return self


class LicenseUpdate(UTCModel):
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    max_cameras: int | None = Field(default=None, ge=1, le=64)
    features: dict | None = None
    analytics_modules: list[str] | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self):
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self
