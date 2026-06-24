import pytest
from openminion.modules.memory.errors import PromotionDeniedError
from openminion.modules.memory.models import MemoryCandidate, MemoryRecord
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def _approved_candidate(
    cid: str = "c1", key: str | None = None, content: str = "promoted fact"
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=cid,
        session_id="s1",
        proposed_scope="session:s1",
        type="fact",
        content=content,
        status="approved",
        key=key,
    )


def test_promote_creates_record(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test.db")
    store.candidate_put(_approved_candidate())

    record = store.promote_candidate("c1", "global:all")
    assert record.scope == "global:all"
    assert record.content == "promoted fact"

    fetched = store.candidate_get("c1")
    assert fetched.status == "promoted"

    in_db = store.get(record.id)
    assert in_db is not None
    assert in_db.scope == "global:all"


def test_promote_unapproved_fails(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test.db")
    candidate = MemoryCandidate(
        candidate_id="c1",
        session_id="s1",
        proposed_scope="session:s1",
        type="fact",
        content="test",
        status="proposed",
    )
    store.candidate_put(candidate)

    with pytest.raises(PromotionDeniedError):
        store.promote_candidate("c1", "global:all")


def test_promote_key_collision_creates_chain(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "test.db")
    existing = MemoryRecord(
        id="old-rec",
        scope="global:all",
        type="fact",
        key="my-key",
        content="old content",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-01T00:00:00Z",
    )
    store.put(existing)

    store.candidate_put(_approved_candidate(key="my-key", content="new content"))
    record = store.promote_candidate("c1", "global:all")

    assert record.content == "new content"
    assert record.key == "my-key"

    old_rec = store.get("old-rec")
    assert old_rec is not None
    assert old_rec.superseded_by_id == record.id

    new_rec = store.get(record.id)
    assert new_rec.supersedes_id == "old-rec"
