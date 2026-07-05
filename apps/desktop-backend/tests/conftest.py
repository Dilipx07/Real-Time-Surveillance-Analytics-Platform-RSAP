from __future__ import annotations

import base64
import hashlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "cv-engine"))
sys.path.insert(0, str(ROOT / "apps" / "desktop-backend"))


def _key(seed: int) -> str:
    digest = hashlib.sha256(f"rsap-desktop-test-key-{seed}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


os.environ.setdefault("RSAP_ENVIRONMENT", "test")
os.environ.setdefault("RSAP_DATABASE_DRIVER", "sqlite-test")
os.environ.setdefault("RSAP_DATABASE_KEY", _key(1))
os.environ.setdefault("RSAP_FIELD_ENCRYPTION_KEY", _key(2))
os.environ.setdefault("RSAP_DATA_DIR", str(ROOT / ".pytest-data"))
os.environ.setdefault("RSAP_DATABASE_PATH", str(ROOT / ".pytest-data" / "local.db"))
os.environ.setdefault("RSAP_CENTRAL_API_URL", "http://central.test")
os.environ.setdefault("RSAP_FILE_SERVER_URL", "http://files.test")
os.environ.setdefault("RSAP_RETRY_BASE_DELAY_SECONDS", "0")


@pytest.fixture
def settings(tmp_path: Path):
    from app.config import Settings

    return Settings(
        environment="test",
        database_driver="sqlite-test",
        database_key=_key(1),
        field_encryption_key=_key(2),
        data_dir=tmp_path,
        database_path=tmp_path / "local.db",
        central_api_url="http://central.test",
        file_server_url="http://files.test",
        retry_base_delay_seconds=0,
    )


@pytest.fixture(autouse=True)
def desktop_test_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RSAP_ENVIRONMENT", "test")
    monkeypatch.setenv("RSAP_DATABASE_DRIVER", "sqlite-test")
    monkeypatch.setenv("RSAP_DATABASE_KEY", _key(1))
    monkeypatch.setenv("RSAP_FIELD_ENCRYPTION_KEY", _key(2))
    monkeypatch.setenv("RSAP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RSAP_DATABASE_PATH", str(tmp_path / "local.db"))
    monkeypatch.setenv("RSAP_CENTRAL_API_URL", "http://central.test")
    monkeypatch.setenv("RSAP_FILE_SERVER_URL", "http://files.test")
    monkeypatch.setenv("RSAP_RETRY_BASE_DELAY_SECONDS", "0")
