from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True)
class RegisteredPerson:
    id: UUID
    full_name: str
    phone: str
    entry_status: str
    face_image_id: UUID | None
