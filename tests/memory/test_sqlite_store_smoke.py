from pathlib import Path

from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def test_store_initializes_with_pragmas(tmp_path: Path) -> None:
    db_path = tmp_path / "test_store.db"
    store = SQLiteMemoryStore(db_path)
    assert db_path.exists()

    with store._connect() as conn:
        cursor = conn.execute("PRAGMA journal_mode")
        journal_mode = cursor.fetchone()[0]
        assert journal_mode.lower() == "wal"

        cursor = conn.execute("PRAGMA foreign_keys")
        fk_mode = cursor.fetchone()[0]
        assert fk_mode == 1
