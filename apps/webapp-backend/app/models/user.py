from dataclasses import dataclass, field
from uuid import UUID


@dataclass(slots=True)
class CurrentUser:
    id: UUID
    email: str
    role: str
    permissions: list[dict] = field(default_factory=list)
