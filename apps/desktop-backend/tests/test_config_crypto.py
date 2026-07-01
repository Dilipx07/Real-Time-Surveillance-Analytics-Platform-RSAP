from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.crypto import DecryptionError, FieldCipher


def key(byte: bytes = b"x") -> str:
    return base64.urlsafe_b64encode(byte * 32).decode("ascii")


def base(**overrides):
    values = {
        "environment": "production",
        "database_key": key(b"d"),
        "field_encryption_key": key(b"f"),
        "central_api_url": "https://central.example",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    "overrides",
    [
        {"database_key": "short"},
        {"host": "0.0.0.0"},
        {"central_api_url": "https://user:password@central.example"},
        {"central_api_url": "http://central.example"},
        {"database_driver": "sqlite-test"},
    ],
)
def test_invalid_production_configuration_is_rejected(overrides):
    with pytest.raises(ValidationError):
        Settings(**base(**overrides))


def test_plaintext_driver_is_explicitly_test_only(tmp_path):
    settings = Settings(**base(
        environment="test", database_driver="sqlite-test",
        central_api_url="http://central.test", database_path=(tmp_path / "db").resolve(),
    ))
    assert settings.database_driver == "sqlite-test"


def test_field_cipher_binds_ciphertext_to_purpose_and_detects_tampering():
    cipher = FieldCipher(b"k" * 32)
    encrypted = cipher.encrypt("rtsp://user:secret@example/cam", "camera:1")
    assert "secret" not in encrypted
    assert cipher.decrypt(encrypted, "camera:1").startswith("rtsp://")
    with pytest.raises(DecryptionError):
        cipher.decrypt(encrypted, "camera:2")
    damaged = encrypted[:-2] + ("AA" if encrypted[-2:] != "AA" else "BB")
    with pytest.raises(DecryptionError):
        cipher.decrypt(damaged, "camera:1")
