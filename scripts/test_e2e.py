from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib import error, request


def report(name: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"{status}: {name}{suffix}")
    return ok


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def http_json(method: str, url: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, str]:
    req = request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with request.urlopen(req, timeout=15) as response:
            return response.status, response.read().decode("utf-8")
    except error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_env_file(repo_root / ".env")
    backend_url = os.getenv("RSAP_BACKEND_URL", "http://localhost:8000")
    file_url = os.getenv("RSAP_FILE_SERVER_URL", "http://localhost:8002")
    ok = True

    try:
        status, text = http_json("GET", f"{backend_url}/health")
        ok &= report("webapp-backend health", status == 200, text[:160])
    except Exception as exc:
        ok &= report("webapp-backend health", False, str(exc))

    try:
        status, text = http_json("GET", f"{file_url}/health")
        ok &= report("file-server health", status == 200, text[:160])
    except Exception as exc:
        ok &= report("file-server health", False, str(exc))

    try:
        import json

        body = json.dumps({
            "email": os.environ["ADMIN_EMAIL"],
            "password": os.environ["ADMIN_PASSWORD"],
            "device_fingerprint": "rsap-python-e2e",
        }).encode("utf-8")
        status, text = http_json(
            "POST",
            f"{backend_url}/api/v1/auth/login",
            body,
            {"Content-Type": "application/json"},
        )
        data = json.loads(text)
        tokens = data.get("data", {}) if isinstance(data, dict) else {}
        access = tokens.get("access_token")
        session = tokens.get("session_token")
        ok &= report("admin login", status == 200 and bool(access and session), text[:160])
    except Exception as exc:
        access = session = None
        ok &= report("admin login", False, str(exc))

    if access and session:
        try:
            status, text = http_json(
                "GET",
                f"{backend_url}/api/v1/auth/me",
                headers={"Authorization": f"Bearer {access}", "X-Session-Token": session},
            )
            ok &= report("dual-token protected request", status == 200, text[:160])
        except Exception as exc:
            ok &= report("dual-token protected request", False, str(exc))

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
