import sqlite3
from pathlib import Path

from openminion.modules.memory.storage.sqlite.migrations import (
    MIGRATIONS,
    run_migrations,
)


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"

    with sqlite3.connect(db_path) as conn:
        run_migrations(conn)

        cursor = conn.execute("SELECT name FROM migrations ORDER BY version")
        applied = [row[0] for row in cursor.fetchall()]
        assert applied == [migration[1] for migration in MIGRATIONS]

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "memory_records" in tables
        assert "memory_candidates" in tables
        assert "memory_fts" in tables
        assert "memory_tier_transitions" in tables

        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(memory_records)").fetchall()
        }
        assert "tier" in columns
        assert "access_count" in columns
        assert "event_time" in columns
        assert "valid_to" in columns

    with sqlite3.connect(db_path) as conn:
        run_migrations(conn)
        cursor = conn.execute("SELECT count(*) FROM migrations")
        count = cursor.fetchone()[0]
        assert count == len(MIGRATIONS)
