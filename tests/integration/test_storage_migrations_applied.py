from __future__ import annotations

from contextlib import closing
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

from openminion.modules.controlplane.channels.telegram.storage.migrations import (
    list_migrations as list_telegram_migrations,
)
from openminion.modules.controlplane.channels.telegram.storage.store import (
    TelegramPollStateStore,
)
from openminion.modules.controlplane.storage.migrations import (
    run_migrations as run_controlplane_alembic_migrations,
)
from openminion.modules.controlplane.storage.schema import MIGRATIONS as CP_MIGRATIONS
from openminion.modules.controlplane.storage.schema import (
    list_migrations as list_controlplane_migrations,
)
from openminion.modules.controlplane.storage.store import SQLiteControlPlaneStore
from openminion.modules.storage.migrations.alembic import (
    discover_module_alembic_paths,
)
from openminion.modules.storage.migrations.module_ids import schema_head_from_migrations
from openminion.modules.telemetry.storage.migrations import (
    run_migrations as run_telemetry_alembic_migrations,
)

ALEMBIC_BASELINES = {
    "controlplane": (
        run_controlplane_alembic_migrations,
        ["0001_baseline", "0002_index_audit"],
        ("cp_inbound_messages", "cp_outbound_messages", "cp_pair_attempts"),
    ),
    "telemetry": (
        run_telemetry_alembic_migrations,
        ["0001_baseline", "0002_event_v2", "0003_debug_query_indexes"],
        ("events",),
    ),
}


def _query_values(path: Path, sql: str) -> list[object]:
    with sqlite3.connect(path) as conn:
        return [row[0] for row in conn.execute(sql).fetchall()]


def _meta_value(path: Path, key: str) -> str | None:
    with sqlite3.connect(path) as conn:
        row = conn.execute("SELECT value FROM om_meta WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row else None


def _index_names(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    return {str(row[0]) for row in rows}


def test_controlplane_sqlite_store_applies_and_reapplies_migrations(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "controlplane" / "cp.db"
    store = SQLiteControlPlaneStore(db_path)
    store.close()

    assert db_path.exists()
    expected_versions = [version for version, _name, _ddl in CP_MIGRATIONS]
    expected_names = [name for _version, name, _ddl in CP_MIGRATIONS]
    assert (
        _query_values(db_path, "SELECT version FROM cp_migrations ORDER BY version")
        == expected_versions
    )
    assert (
        _query_values(db_path, "SELECT name FROM cp_migrations ORDER BY version")
        == expected_names
    )
    assert _meta_value(db_path, "schema_head") == schema_head_from_migrations(
        list_controlplane_migrations()
    )
    assert {
        "idx_cp_inbox_status_locked",
        "idx_cp_outbox_status_locked",
        "idx_cp_channel_subjects_channel_status",
        "idx_cp_pairings_status_channel",
    }.issubset(_index_names(db_path))

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "DELETE FROM cp_migrations WHERE version = ?", (expected_versions[-1],)
        )
        conn.commit()
    store = SQLiteControlPlaneStore(db_path)
    store.close()
    assert (
        _query_values(db_path, "SELECT version FROM cp_migrations ORDER BY version")
        == expected_versions
    )


def test_telegram_poll_state_store_applies_schema_metadata_and_indexes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "controlplane" / "telegram-poll-state.db"
    store = TelegramPollStateStore(db_path)
    store.close()

    assert db_path.exists()
    assert _meta_value(db_path, "schema_head") == schema_head_from_migrations(
        list_telegram_migrations()
    )
    with sqlite3.connect(db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        }
    assert {
        "telegram_poll_state",
        "telegram_pair_tokens",
        "telegram_pair_attempts",
        "telegram_pending_clarify",
        "telegram_polling_leases",
    }.issubset(tables)
    assert "idx_telegram_polling_leases_heartbeat" in _index_names(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM om_meta WHERE key = 'schema_head'")
        conn.commit()
    store = TelegramPollStateStore(db_path)
    store.close()
    assert _meta_value(db_path, "schema_head") == schema_head_from_migrations(
        list_telegram_migrations()
    )


def _sqlite_master(path: Path) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()


def _alembic_config(module_id: str, db_path: Path) -> Config:
    ini_path, script_location = discover_module_alembic_paths(module_id)
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(script_location))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


@pytest.mark.parametrize("module_id", sorted(ALEMBIC_BASELINES))
def test_sqlite_baseline_applies_its_sqlite_ddl_as_is(
    tmp_path: Path, module_id: str
) -> None:
    _run, revisions, autoincrement_tables = ALEMBIC_BASELINES[module_id]
    db_path = tmp_path / f"{module_id}.db"
    config = _alembic_config(module_id, db_path)
    script = ScriptDirectory.from_config(config)
    baseline = script.get_revision("0001_baseline")
    assert baseline is not None
    reference = tmp_path / "reference.db"
    with closing(sqlite3.connect(reference)) as conn:
        for statement in baseline.module.DDL:
            conn.execute(str(statement).strip())
        conn.commit()

    command.upgrade(config, "0001_baseline")

    assert [
        row for row in _sqlite_master(db_path) if row[2] != "alembic_version"
    ] == _sqlite_master(reference)
    assert [rev.revision for rev in script.walk_revisions()] == revisions[::-1]
    table_sql = {row[1]: str(row[3]) for row in _sqlite_master(db_path)}
    for table_name in autoincrement_tables:
        assert "id INTEGER PRIMARY KEY AUTOINCREMENT" in table_sql[table_name]
    if module_id == "telemetry":
        assert "timestamp REAL NOT NULL" in table_sql["events"]


@pytest.mark.parametrize("module_id", sorted(ALEMBIC_BASELINES))
def test_sqlite_upgrade_of_a_migrated_file_changes_nothing(
    tmp_path: Path, module_id: str
) -> None:
    run_migrations, revisions, _tables = ALEMBIC_BASELINES[module_id]
    db_path = tmp_path / f"{module_id}.db"
    run_migrations(db_path)
    schema = _sqlite_master(db_path)
    assert _query_values(db_path, "SELECT version_num FROM alembic_version") == [
        revisions[-1]
    ]

    run_migrations(db_path)

    assert _sqlite_master(db_path) == schema
    assert _query_values(db_path, "SELECT version_num FROM alembic_version") == [
        revisions[-1]
    ]


def test_controlplane_store_opens_a_file_the_alembic_baseline_created(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cp.db"
    run_controlplane_alembic_migrations(db_path)
    schema = _sqlite_master(db_path)

    store = SQLiteControlPlaneStore(db_path)
    try:
        inbound_id = store.put_inbound(
            chat_key="chat:1",
            user_key="user:1",
            text="hello",
            payload={"message_id": "message-1"},
            session_id="session-1",
        )
        turns = store.list_turns("session-1")
    finally:
        store.close()

    assert [(turn["id"], turn["text"]) for turn in turns] == [(inbound_id, "hello")]
    assert _query_values(
        db_path, "SELECT version FROM cp_migrations ORDER BY version"
    ) == [version for version, _name, _ddl in CP_MIGRATIONS]
    assert _sqlite_master(db_path) == schema
    assert _query_values(db_path, "SELECT version_num FROM alembic_version") == [
        "0002_index_audit"
    ]


def test_controlplane_store_still_adds_the_retry_column_to_an_older_file(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "cp.db"
    with closing(sqlite3.connect(db_path)) as conn:
        for version, name, ddl in CP_MIGRATIONS[:5]:
            conn.executescript(ddl)
            conn.execute(
                "INSERT INTO cp_migrations(version, name, applied_at) VALUES (?,?,?)",
                (version, name, "2026-01-01T00:00:00+00:00"),
            )
        conn.commit()
    assert "next_attempt_at" not in _query_values(
        db_path, "SELECT name FROM pragma_table_info('cp_inbox')"
    )

    SQLiteControlPlaneStore(db_path).close()

    assert "next_attempt_at" in _query_values(
        db_path, "SELECT name FROM pragma_table_info('cp_inbox')"
    )
    assert "idx_cp_inbox_status_next" in _index_names(db_path)
    assert _query_values(
        db_path, "SELECT version FROM cp_migrations ORDER BY version"
    ) == [version for version, _name, _ddl in CP_MIGRATIONS]
