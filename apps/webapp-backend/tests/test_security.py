from datetime import timedelta

import pytest
from jose import JWTError

from app.encryption import decrypt_text, encrypt_text
from app.security import create_token, decode_token, hash_password, verify_password


def test_aes_gcm_round_trip_uses_random_nonce():
    first = encrypt_text("9876")
    second = encrypt_text("9876")
    assert first != second
    assert decrypt_text(first) == "9876"
    assert decrypt_text(second) == "9876"


def test_password_hash_and_verification():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_jwt_type_is_enforced():
    access, jti = create_token(
        "48ef73b8-02c3-48cd-b8f1-251dcf5199ce",
        "access",
        timedelta(minutes=5),
        "session-id",
    )
    claims = decode_token(access)
    assert claims["type"] == "access"
    assert claims["sid"] == "session-id"
    assert claims["jti"] == jti
    with pytest.raises(JWTError):
        decode_token(access, expected_type="refresh")
