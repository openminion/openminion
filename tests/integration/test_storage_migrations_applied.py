from __future__ import annotations

import sqlite3
from pathlib import Path

from openminion.modules.controlplane.channels.telegram.storage.migrations import (
    list_migrations as list_telegram_migrations,
)
from openminion.modules.controlplane.channels.telegram.storage.store import (
    TelegramPollStateStore,
)
from openminion.modules.controlplane.storage.schema import MIGRATIONS as CP_MIGRATIONS
from openminion.modules.controlplane.storage.schema import (
    list_migrations as list_controlplane_migrations,
)
from openminion.modules.controlplane.storage.store import SQLiteControlPlaneStore
from openminion.modules.storage.migrations.module_ids import schema_head_from_migrations


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


def test_controlplane_sqlite_store_applies_and_reapplies_migrations(tmp_path: Path) -> None:
    db_path = tmp_path / "controlplane" / "cp.db"
    store = SQLiteControlPlaneStore(db_path)
    store.close()

    assert db_path.exists()
    expected_versions = [version for version, _name, _ddl in CP_MIGRATIONS]
    expected_names = [name for _version, name, _ddl in CP_MIGRATIONS]
    assert _query_values(db_path, "SELECT version FROM cp_migrations ORDER BY version") == expected_versions
    assert _query_values(db_path, "SELECT name FROM cp_migrations ORDER BY version") == expected_names
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
        conn.execute("DELETE FROM cp_migrations WHERE version = ?", (expected_versions[-1],))
        conn.commit()
    store = SQLiteControlPlaneStore(db_path)
    store.close()
    assert _query_values(db_path, "SELECT version FROM cp_migrations ORDER BY version") == expected_versions


def test_telegram_poll_state_store_applies_schema_metadata_and_indexes(tmp_path: Path) -> None:
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
