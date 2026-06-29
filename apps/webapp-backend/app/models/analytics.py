from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class AnalyticsEvent:
    id: UUID
    camera_id: UUID
    event_type: str
    created_at: datetime
    payload: dict = field(default_factory=dict)
