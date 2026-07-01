"""SQLCipher connection management and deterministic migrations."""

from __future__ import annotations

import asyncio
import hashlib
import platform
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from app.config import Settings

T = TypeVar("T")


class MigrationError(RuntimeError):
    """Raised when migration history is invalid or an upgrade fails."""


class Database:
    """Small async boundary over one-connection-per-transaction SQLite access."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.database_path
        if self.path is None:
            raise ValueError("database path was not resolved")
        self._closed = False
        self._migration_lock = asyncio.Lock()

    def _driver(self) -> Any:
        if self.settings.database_driver == "sqlite-test":
            return sqlite3
        try:
            from sqlcipher3 import dbapi2 as sqlcipher
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise RuntimeError("SQLCipher driver is required for non-test databases") from exc
        return sqlcipher

    def connect(self) -> Any:
        if self._closed:
            raise RuntimeError("database is closed")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        driver = self._driver()
        connection = driver.connect(str(self.path), timeout=10.0, isolation_level=None)
        connection.row_factory = driver.Row
        if self.settings.database_driver == "sqlcipher":
            key_hex = self.settings.database_key_bytes.hex()
            connection.execute(f"PRAGMA key = \"x'{key_hex}'\"")
            cipher_version = connection.execute("PRAGMA cipher_version").fetchone()
            if not cipher_version or not cipher_version[0]:
                connection.close()
                raise RuntimeError("database driver does not provide SQLCipher")
            # SQLCipher 4.12 on Windows can recurse in VirtualLock while enabling
            # memory security. The database and sensitive fields remain encrypted;
            # enable locked-memory protection on platforms where it is stable.
            if platform.system() != "Windows":
                connection.execute("PRAGMA cipher_memory_security = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    async def migrate(self) -> None:
        async with self._migration_lock:
            await asyncio.to_thread(self._migrate_sync)

    def _migrate_sync(self) -> None:
        migrations_dir = Path(__file__).with_name("migrations")
        migrations = sorted(migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        if not migrations:
            raise MigrationError("no desktop database migrations were found")
        connection = self.connect()
        try:
            connection.execute("BEGIN EXCLUSIVE")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations (
                       version TEXT PRIMARY KEY,
                       checksum TEXT NOT NULL,
                       applied_at TEXT NOT NULL
                   )"""
            )
            applied = {
                row["version"]: row["checksum"]
                for row in connection.execute(
                    "SELECT version, checksum FROM schema_migrations"
                ).fetchall()
            }
            for migration in migrations:
                version = migration.name.split("_", 1)[0]
                sql = migration.read_text(encoding="utf-8-sig")
                checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                if version in applied:
                    if applied[version] != checksum:
                        raise MigrationError(f"migration {version} checksum changed")
                    continue
                for statement in (part.strip() for part in sql.split(";")):
                    if statement:
                        connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, checksum, applied_at) "
                    "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (version, checksum),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def read(self, operation: Callable[[Any], T]) -> T:
        return await asyncio.to_thread(self._run_sync, operation, False)

    async def write(self, operation: Callable[[Any], T]) -> T:
        return await asyncio.to_thread(self._run_sync, operation, True)

    def _run_sync(self, operation: Callable[[Any], T], write: bool) -> T:
        connection = self.connect()
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            result = operation(connection)
            if write:
                connection.commit()
            return result
        except Exception:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    async def verify(self) -> None:
        def check(connection: Any) -> None:
            connection.execute("SELECT count(*) FROM schema_migrations").fetchone()

        await self.read(check)

    async def close(self) -> None:
        self._closed = True
