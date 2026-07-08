from __future__ import annotations

import os
import sys
from pathlib import Path

REQUIRED = [
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "REDIS_URL",
    "MINIO_ENDPOINT",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "JWT_SECRET",
    "AES_ENCRYPTION_KEY",
    "LICENSE_SIGNING_SECRET",
    "ADMIN_EMAIL",
    "ADMIN_PASSWORD",
    "FILE_SERVER_SERVICE_TOKEN",
]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_env_file(repo_root / ".env")
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        print("Missing required environment variables:")
        for name in missing:
            print(f"  - {name}")
        return 1
    unsafe = [
        name
        for name in REQUIRED
        if "change_me" in os.getenv(name, "").lower() or os.getenv(name, "").startswith("<required")
    ]
    if unsafe:
        print("Replace placeholder values before starting RSAP:")
        for name in unsafe:
            print(f"  - {name}")
        return 1
    print("Environment file contains required RSAP settings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
