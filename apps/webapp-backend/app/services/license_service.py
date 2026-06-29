import hashlib
import hmac
from datetime import datetime
from uuid import UUID

from app.config import get_settings


def generate_license_key(user_id: UUID, valid_until: datetime) -> str:
    message = f"{user_id}:{valid_until.isoformat()}".encode("utf-8")
    return hmac.new(
        get_settings().license_signing_secret.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()
