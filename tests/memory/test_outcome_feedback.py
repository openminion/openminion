from __future__ import annotations

from datetime import datetime, timezone

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_sqlite_outcome_feedback_clamps_and_updates_meta(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    now = _now()
    record = MemoryRecord(
        id="mem_sqlite_1",
        scope="agent:test",
        type="fact",
        title="Deploy note",
        content="Run smoke tests after deploy.",
        created_at=now,
        updated_at=now,
        meta={"feedback_score": 0.95},
    )
    store.put(record)

    updated = service.apply_outcome_feedback(
        record_ids=["mem_sqlite_1"],
        outcome="success",
        command_id="cmd-sqlite-1",
        observed_at=now,
        feedback_delta=0.2,
    )
    stored = service.get("mem_sqlite_1")

    assert updated == 1
    assert stored.meta["feedback_score"] == 1.0
    assert stored.meta["outcome_success_count"] == 1
    assert stored.meta["last_outcome_status"] == "success"


def test_inmemory_outcome_feedback_skips_deleted_and_superseded_records() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    now = _now()
    active = MemoryRecord(
        id="mem_active",
        scope="agent:test",
        type="fact",
        title="Active",
        content="Active content",
        created_at=now,
        updated_at=now,
    )
    deleted = MemoryRecord(
        id="mem_deleted",
        scope="agent:test",
        type="fact",
        title="Deleted",
        content="Deleted content",
        created_at=now,
        updated_at=now,
        is_deleted=True,
    )
    superseded = MemoryRecord(
        id="mem_superseded",
        scope="agent:test",
        type="fact",
        title="Superseded",
        content="Superseded content",
        created_at=now,
        updated_at=now,
        superseded_by_id="mem_newer",
    )
    store.put(active)
    store.put(deleted)
    store.put(superseded)

    updated = service.apply_outcome_feedback(
        record_ids=["mem_active", "mem_deleted", "mem_superseded", "missing"],
        outcome="failed",
        command_id="cmd-inmem-1",
        observed_at=now,
        feedback_delta=-0.2,
    )
    active_record = service.get("mem_active")

    assert updated == 1
    assert active_record.meta["feedback_score"] == 0.0
    assert active_record.meta["outcome_failure_count"] == 1
