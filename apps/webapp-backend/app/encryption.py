import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings


def _key() -> bytes:
    return hashlib.sha256(get_settings().aes_encryption_key.encode("utf-8")).digest()


def encrypt_text(value: str) -> str:
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key()).encrypt(nonce, value.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_text(value: str) -> str:
    payload = base64.urlsafe_b64decode(value.encode("ascii"))
    return AESGCM(_key()).decrypt(payload[:12], payload[12:], None).decode("utf-8")
