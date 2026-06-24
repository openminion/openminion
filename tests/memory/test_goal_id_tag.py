from __future__ import annotations

from openminion.modules.memory.models import MemoryRecord
from openminion.modules.memory.runtime.remote_transport import (
    RemoteMemoryStore,
    RemoteMemoryTransport,
)
from openminion.modules.memory.storage.base import SearchQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def _record(
    record_id: str, goal_id: str | None, *, scope: str = "session:goal"
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope=scope,
        type="fact",
        title=f"title {record_id}",
        content={"text": f"content {record_id}"},
        goal_id=goal_id,
        source="validated",
        confidence=0.9,
        created_at="2026-05-24T00:00:00Z",
        updated_at="2026-05-24T00:00:00Z",
    )


def test_sqlite_memory_store_round_trips_goal_id_and_filters_by_goal(tmp_path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    store.put(_record("rec-goal-a", "goal-a"))
    store.put(_record("rec-goal-b", "goal-b"))
    store.put(_record("rec-goal-a-2", "goal-a"))
    store.put(_record("rec-no-goal", None))

    fetched = store.get("rec-goal-a")
    goal_hits = store.list_records_by_goal_id("goal-a", scopes=["session:goal"])
    blank_hits = store.list_records_by_goal_id("   ")

    assert fetched is not None
    assert fetched.goal_id == "goal-a"
    assert {item.id for item in goal_hits} == {"rec-goal-a", "rec-goal-a-2"}
    assert blank_hits == []


def test_remote_memory_store_parses_goal_id_from_transport_payload() -> None:
    records = [
        {
            "id": "remote-goal",
            "scope": "session:goal",
            "type": "fact",
            "title": "remote title",
            "content": {"text": "remote body"},
            "goal_id": "goal-remote",
            "source": "validated",
            "confidence": 0.8,
            "created_at": "2026-05-24T00:00:00Z",
            "updated_at": "2026-05-24T00:00:00Z",
        }
    ]

    def _sender(envelope: dict[str, object], timeout: float) -> dict[str, object]:
        del timeout
        if envelope["operation"] == "search":
            return {"ok": True, "data": {"records": records}}
        return {"ok": True, "data": {}}

    store = RemoteMemoryStore(
        RemoteMemoryTransport(
            endpoint="https://example.invalid/memory",
            sender=_sender,
        )
    )

    hits = store.search(
        SearchQueryOptions(query="remote", scopes=["session:goal"], limit=5)
    )

    assert len(hits) == 1
    assert hits[0].goal_id == "goal-remote"
