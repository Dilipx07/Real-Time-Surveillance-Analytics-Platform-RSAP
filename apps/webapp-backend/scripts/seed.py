from __future__ import annotations

import asyncio
import os
import socket
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import asyncpg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[3]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.security import hash_password  # noqa: E402
from app.services.license_service import generate_license_key  # noqa: E402


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value or value.startswith("<required") or "change_me" in value.lower():
        raise RuntimeError(f"{name} must be set to a non-placeholder value")
    return value


async def ensure_admin() -> None:
    load_dotenv(ROOT / ".env")
    email = require_env("ADMIN_EMAIL").strip().lower()
    password = require_env("ADMIN_PASSWORD")
    host = os.getenv("POSTGRES_HOST", "localhost")
    if host == "postgres" and not _host_resolves(host):
        host = "localhost"

    connection = await asyncpg.connect(
        host=host,
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=require_env("POSTGRES_DB"),
        user=require_env("POSTGRES_USER"),
        password=require_env("POSTGRES_PASSWORD"),
    )
    try:
        async with connection.transaction():
            admin = await connection.fetchrow(
                "SELECT id, is_active, is_deleted FROM auth.users WHERE lower(email)=lower($1) FOR UPDATE",
                email,
            )
            if admin is None:
                admin = await connection.fetchrow(
                    """INSERT INTO auth.users(email, password_hash, role, is_active, is_deleted)
                       VALUES($1, $2, 'super_admin', true, false)
                       RETURNING id, is_active, is_deleted""",
                    email,
                    await asyncio.to_thread(hash_password, password),
                )
                print(f"Created admin user {email}")
            else:
                await connection.execute(
                    """UPDATE auth.users
                       SET role='super_admin', is_active=true, is_deleted=false, updated_at=NOW()
                       WHERE id=$1""",
                    admin["id"],
                )
                print(f"Ensured admin user {email}")

            admin_id: UUID = admin["id"]
            now = datetime.now(UTC)
            valid_until = now + timedelta(days=365)
            existing_license = await connection.fetchrow(
                """SELECT id, valid_until FROM rbac.licenses
                   WHERE user_id=$1 AND is_active=true AND valid_until > NOW()
                   ORDER BY valid_until DESC LIMIT 1 FOR UPDATE""",
                admin_id,
            )
            if existing_license is None:
                await connection.execute(
                    """INSERT INTO rbac.licenses(
                           user_id, license_key, features, max_cameras, analytics_modules,
                           valid_from, valid_until, is_active, created_by
                       )
                       VALUES($1, $2, $3::jsonb, 16, $4::jsonb, $5, $6, true, $1)""",
                    admin_id,
                    generate_license_key(admin_id, valid_until),
                    '{"admin_console": true, "desktop_sync": true}',
                    '["people_counting", "intrusion", "face_recognition"]',
                    now,
                    valid_until,
                )
                print("Created active admin license")
            else:
                print("Active admin license already exists")

            await connection.execute(
                """INSERT INTO audit.logs(user_id, action, resource, metadata)
                   VALUES($1, 'seed_admin', 'auth.user', jsonb_build_object('email', $2::text))""",
                admin_id,
                email,
            )
    finally:
        await connection.close()


def _host_resolves(host: str) -> bool:
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return True


def main() -> int:
    try:
        asyncio.run(ensure_admin())
    except Exception as exc:
        print(f"Admin seed failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
