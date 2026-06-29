from dataclasses import dataclass, field
from uuid import UUID


@dataclass(slots=True)
class Camera:
    id: UUID
    user_id: UUID
    name: str
    stream_type: str
    analytics_config: dict = field(default_factory=dict)
    zones: list[dict] = field(default_factory=list)
