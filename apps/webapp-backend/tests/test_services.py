from datetime import UTC, datetime
from uuid import uuid4

from app.services.license_service import generate_license_key


def test_license_key_is_deterministic_and_bound_to_expiry():
    user_id = uuid4()
    first_expiry = datetime(2030, 1, 1, tzinfo=UTC)
    second_expiry = datetime(2031, 1, 1, tzinfo=UTC)
    assert generate_license_key(user_id, first_expiry) == generate_license_key(user_id, first_expiry)
    assert generate_license_key(user_id, first_expiry) != generate_license_key(user_id, second_expiry)
