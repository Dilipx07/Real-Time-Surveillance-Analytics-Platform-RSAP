from __future__ import annotations

import base64
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
    assert [row["version"] for row in versions] == ["0001", "0002", "0003"]
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
async def test_unknown_migration_history_is_rejected(settings):
    database = Database(settings)
    await database.migrate()
    await database.write(lambda connection: connection.execute(
        "INSERT INTO schema_migrations VALUES('9999','unknown','2030-01-01T00:00:00Z')"
    ))
    with pytest.raises(MigrationError, match="unknown applied"):
        await database.migrate()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([('0002', 'checksum')], "gap"),
        ([('0000', 'removed')], "unknown applied"),
        ([('0001', 'one'), ('0001', 'two')], "duplicate applied"),
    ],
)
async def test_migration_history_gap_removed_and_duplicate_are_rejected(settings, rows, message):
    connection = sqlite3.connect(settings.database_path)
    connection.execute("CREATE TABLE schema_migrations(version TEXT, checksum TEXT, applied_at TEXT)")
    connection.executemany(
        "INSERT INTO schema_migrations VALUES(?,?, '2030-01-01T00:00:00Z')", rows
    )
    connection.commit()
    connection.close()
    database = Database(settings)
    with pytest.raises(MigrationError, match=message):
        await database.migrate()


def test_partial_migration_failure_rolls_back(settings):
    database = Database(settings)
    real = sqlite3.connect(settings.database_path, isolation_level=None)
    real.row_factory = sqlite3.Row

    class FailingConnection:
        def execute(self, statement, parameters=()):
            if statement.startswith("CREATE TABLE local_cameras"):
                raise sqlite3.OperationalError("injected migration failure")
            return real.execute(statement, parameters)

        def commit(self):
            real.commit()

        def rollback(self):
            real.rollback()

        def close(self):
            real.close()

    database.connect = lambda: FailingConnection()
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        database._migrate_sync()
    check = sqlite3.connect(settings.database_path)
    assert check.execute(
        "SELECT count(*) FROM sqlite_master WHERE name='schema_migrations'"
    ).fetchone()[0] == 0
    check.close()


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
    await database.write(lambda connection: connection.execute(
        "INSERT INTO local_runtime_state VALUES(?, ?, ?)",
        ("probe", '"sensitive-runtime-value"', "2030-01-01T00:00:00Z"),
    ))
    await database.close()
    raw = secure.database_path.read_bytes()
    assert b"schema_migrations" not in raw
    assert b"sensitive-runtime-value" not in raw
    with pytest.raises(sqlite3.DatabaseError):
        plain = sqlite3.connect(secure.database_path)
        try:
            plain.execute("SELECT * FROM schema_migrations").fetchall()
        finally:
            plain.close()

    wrong_key = base64.urlsafe_b64encode(bytes(range(64, 96))).decode("ascii")
    wrong_settings = Settings(
        environment="test", database_driver="sqlcipher",
        database_key=wrong_key,
        field_encryption_key=settings.field_encryption_key,
        database_path=secure.database_path,
        data_dir=tmp_path,
        central_api_url="http://central.test",
    )
    wrong_database = Database(wrong_settings)
    with pytest.raises(Exception, match="encrypted|database|file"):
        await wrong_database.verify()
