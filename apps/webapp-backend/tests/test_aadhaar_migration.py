import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0001_encrypt_aadhaar.py"
)


@pytest.fixture
def migration_module():
    spec = importlib.util.spec_from_file_location("migration_0001_encrypt_aadhaar", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeInspector:
    def __init__(
        self,
        *,
        schema_exists: bool = True,
        table_exists: bool = True,
        columns: list[dict] | None = None,
    ):
        self.schema_exists = schema_exists
        self.table_exists = table_exists
        self.columns = columns or []

    def has_schema(self, schema_name: str) -> bool:
        assert schema_name == "events"
        return self.schema_exists

    def has_table(self, table_name: str, schema: str | None = None) -> bool:
        assert table_name == "registered_persons"
        assert schema == "events"
        return self.table_exists

    def get_columns(self, table_name: str, schema: str | None = None) -> list[dict]:
        assert table_name == "registered_persons"
        assert schema == "events"
        return self.columns


def run_upgrade(monkeypatch, migration_module, inspector):
    altered = []
    monkeypatch.setattr(migration_module.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration_module.sa, "inspect", lambda bind: inspector)
    monkeypatch.setattr(
        migration_module.op,
        "alter_column",
        lambda *args, **kwargs: altered.append((args, kwargs)),
    )
    migration_module.upgrade()
    return altered


def test_upgrade_is_noop_when_table_does_not_exist(monkeypatch, migration_module):
    altered = run_upgrade(
        monkeypatch, migration_module, FakeInspector(table_exists=False)
    )
    assert altered == []


def test_upgrade_is_noop_when_schema_does_not_exist(monkeypatch, migration_module):
    altered = run_upgrade(
        monkeypatch, migration_module, FakeInspector(schema_exists=False)
    )
    assert altered == []


def test_upgrade_is_noop_when_column_does_not_exist(monkeypatch, migration_module):
    altered = run_upgrade(monkeypatch, migration_module, FakeInspector(columns=[]))
    assert altered == []


def test_upgrade_is_noop_when_column_is_already_text(monkeypatch, migration_module):
    altered = run_upgrade(
        monkeypatch,
        migration_module,
        FakeInspector(
            table_exists=True,
            columns=[{"name": "aadhaar_last4", "type": sa.Text(), "nullable": False}],
        ),
    )
    assert altered == []


def test_upgrade_changes_legacy_char_four_to_text(monkeypatch, migration_module):
    altered = run_upgrade(
        monkeypatch,
        migration_module,
        FakeInspector(
            table_exists=True,
            columns=[{"name": "aadhaar_last4", "type": sa.CHAR(4), "nullable": False}],
        ),
    )
    assert len(altered) == 1
    args, kwargs = altered[0]
    assert args == ("registered_persons", "aadhaar_last4")
    assert kwargs["schema"] == "events"
    assert isinstance(kwargs["existing_type"], sa.CHAR)
    assert isinstance(kwargs["type_"], sa.Text)
    assert kwargs["existing_nullable"] is False


def test_upgrade_changes_legacy_varchar_to_text(monkeypatch, migration_module):
    altered = run_upgrade(
        monkeypatch,
        migration_module,
        FakeInspector(
            columns=[
                {"name": "aadhaar_last4", "type": sa.VARCHAR(255), "nullable": True}
            ],
        ),
    )
    assert len(altered) == 1
    _, kwargs = altered[0]
    assert isinstance(kwargs["existing_type"], sa.VARCHAR)
    assert isinstance(kwargs["type_"], sa.Text)
    assert kwargs["existing_nullable"] is True


def test_upgrade_is_noop_for_unexpected_column_type(monkeypatch, migration_module):
    altered = run_upgrade(
        monkeypatch,
        migration_module,
        FakeInspector(
            columns=[
                {"name": "aadhaar_last4", "type": sa.Integer(), "nullable": False}
            ],
        ),
    )
    assert altered == []


def test_downgrade_remains_intentionally_irreversible(migration_module):
    with pytest.raises(RuntimeError, match="cannot be safely converted"):
        migration_module.downgrade()
