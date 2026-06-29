from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

EmailString = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        to_lower=True,
        min_length=3,
        max_length=255,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class UTCModel(StrictModel):
    @field_validator("*", mode="after")
    @classmethod
    def require_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("datetime must include a timezone")
            return value.astimezone(UTC)
        return value
