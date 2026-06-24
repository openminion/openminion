from pathlib import Path

import pytest

from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


@pytest.fixture
def store(tmp_path: Path) -> SQLiteMemoryStore:
    return SQLiteMemoryStore(tmp_path / "test.db")


def test_upsert_creates_new_record_if_not_exists(store: SQLiteMemoryStore) -> None:
    rec = store.upsert(
        scope="session:1",
        type="fact",
        key="user_name",
        record_patch={"content": {"name": "Alice"}, "title": "User Name"},
    )
    assert rec.id is not None
    assert rec.content["name"] == "Alice"
    assert rec.title == "User Name"


def test_upsert_supersedes_existing_record(store: SQLiteMemoryStore) -> None:
    first = store.upsert(
        scope="session:1",
        type="fact",
        key="theme",
        record_patch={"content": {"color": "red"}},
    )
    second = store.upsert(
        scope="session:1",
        type="fact",
        key="theme",
        record_patch={"content": {"color": "blue"}},
    )
    assert first.id != second.id
    assert second.supersedes_id == first.id

    first_refreshed = store.get(first.id)
    assert first_refreshed.superseded_by_id == second.id


def test_history_chain(store: SQLiteMemoryStore) -> None:
    v1 = store.upsert("session:A", "pin", "k1", {"content": "v1"})
    v2 = store.upsert("session:A", "pin", "k1", {"content": "v2"})
    v3 = store.upsert("session:A", "pin", "k1", {"content": "v3"})

    hist = store.history("session:A", "pin", "k1")
    assert len(hist) == 3
    assert hist[0].id == v3.id
    assert hist[1].id == v2.id
    assert hist[2].id == v1.id


def test_tombstone(store: SQLiteMemoryStore) -> None:
    rec = store.upsert("session:1", "pin", "k2", {"content": "abc"})
    store.tombstone("session:1", "pin", "k2")

    tombstoned = store.get(rec.id)
    assert tombstoned.is_deleted is True

    rec2 = store.upsert("session:1", "pin", "k2", {"content": "def"})
    assert rec2.supersedes_id is None
