from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(slots=True)
class License:
    id: UUID
    user_id: UUID
    valid_from: datetime
    valid_until: datetime
    is_active: bool
    max_cameras: int
