from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.config import Settings
from app.database import Database, MigrationError


@pytest.mark.asyncio
async def test_empty_database_upgrade_is_complete_and_idempotent(settings):
    database = Database(settings)
    await database.migrate()
    await database.migrate()
    versions = await database.read(lambda connection: connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall())
    assert [row["version"] for row in versions] == ["0001", "0002"]
    await database.close()


@pytest.mark.asyncio
async def test_upgrade_from_prior_schema(settings):
    migration = Path(__file__).parents[1] / "app" / "migrations" / "0001_initial.sql"
    sql = migration.read_text(encoding="utf-8-sig")
    connection = sqlite3.connect(settings.database_path)
    connection.executescript(sql)
    connection.execute(
        "CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, checksum TEXT, applied_at TEXT)"
    )
    connection.execute(
        "INSERT INTO schema_migrations VALUES (?, ?, ?)",
        ("0001", hashlib.sha256(sql.encode()).hexdigest(), "2026-01-01T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()

    database = Database(settings)
    await database.migrate()
    columns = await database.read(lambda conn: conn.execute("PRAGMA table_info(sync_queue)").fetchall())
    assert "lease_owner" in {row["name"] for row in columns}


@pytest.mark.asyncio
async def test_changed_migration_checksum_is_rejected(settings):
    database = Database(settings)
    await database.migrate()
    await database.write(lambda connection: connection.execute(
        "UPDATE schema_migrations SET checksum='wrong' WHERE version='0001'"
    ))
    with pytest.raises(MigrationError, match="checksum changed"):
        await database.migrate()


@pytest.mark.asyncio
async def test_sqlcipher_file_is_not_plain_sqlite(settings, tmp_path):
    secure = Settings(
        environment="test", database_driver="sqlcipher",
        database_key=settings.database_key,
        field_encryption_key=settings.field_encryption_key,
        database_path=(tmp_path / "encrypted.db").resolve(),
        central_api_url="http://central.test",
    )
    database = Database(secure)
    await database.migrate()
    await database.verify()
    with pytest.raises(sqlite3.DatabaseError):
        plain = sqlite3.connect(secure.database_path)
        try:
            plain.execute("SELECT * FROM schema_migrations").fetchall()
        finally:
            plain.close()

