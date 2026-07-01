from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KEY = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
os.environ.setdefault("RSAP_ENVIRONMENT", "test")
os.environ.setdefault("RSAP_DATABASE_DRIVER", "sqlite-test")
os.environ.setdefault("RSAP_DATABASE_KEY", KEY)
os.environ.setdefault("RSAP_FIELD_ENCRYPTION_KEY", KEY)
os.environ.setdefault("RSAP_CENTRAL_API_URL", "http://central.test")
os.environ.setdefault("RSAP_DATABASE_PATH", str((ROOT / "test-import.db").resolve()))

from app.config import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        database_driver="sqlite-test",
        database_key=KEY,
        field_encryption_key=base64.urlsafe_b64encode(b"f" * 32).decode("ascii"),
        database_path=(tmp_path / "local.db").resolve(),
        data_dir=tmp_path,
        central_api_url="http://central.test",
        retry_attempts=3,
        retry_base_delay_seconds=0,
    )

