"""Authenticated field encryption for sensitive local values."""

from __future__ import annotations

import base64
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class DecryptionError(ValueError):
    """Raised when encrypted local data is corrupt or uses the wrong key."""


class FieldCipher:
    """AES-256-GCM field encryption with explicit purpose binding."""

    VERSION = b"v1"

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("field encryption key must contain 32 bytes")
        self._aes = AESGCM(key)

    def encrypt(self, plaintext: str, purpose: str) -> str:
        nonce = os.urandom(12)
        ciphertext = self._aes.encrypt(nonce, plaintext.encode("utf-8"), purpose.encode("utf-8"))
        return base64.urlsafe_b64encode(self.VERSION + nonce + ciphertext).decode("ascii")

    def decrypt(self, encoded: str, purpose: str) -> str:
        try:
            payload = base64.urlsafe_b64decode(encoded.encode("ascii"))
            if payload[:2] != self.VERSION or len(payload) < 31:
                raise DecryptionError("unsupported encrypted value")
            return self._aes.decrypt(
                payload[2:14], payload[14:], purpose.encode("utf-8")
            ).decode("utf-8")
        except (InvalidTag, ValueError, UnicodeError) as exc:
            raise DecryptionError("encrypted local value could not be authenticated") from exc

    def encrypt_json(self, value: Any, purpose: str) -> str:
        return self.encrypt(json.dumps(value, separators=(",", ":"), sort_keys=True), purpose)

    def decrypt_json(self, encoded: str, purpose: str) -> Any:
        try:
            return json.loads(self.decrypt(encoded, purpose))
        except json.JSONDecodeError as exc:
            raise DecryptionError("encrypted local JSON is invalid") from exc

