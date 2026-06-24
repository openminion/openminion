from __future__ import annotations

import datetime
from pathlib import Path
import tempfile

import pytest

from openminion.modules.memory.models import (
    CandidateReview,
    MemoryCandidate,
    MemoryRecord,
)
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
    RecordOrder,
    SearchQueryOptions,
)
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _record(
    record_id: str,
    *,
    scope: str = "session:s1",
    record_type: str = "fact",
    content: dict[str, object] | str = "alpha memory",
    key: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    entities: list[str] | None = None,
) -> MemoryRecord:
    now = _now()
    return MemoryRecord(
        id=record_id,
        scope=scope,
        type=record_type,
        key=key,
        title=title,
        content=content,
        tags=list(tags or []),
        entities=list(entities or []),
        created_at=now,
        updated_at=now,
    )


def _candidate(
    candidate_id: str,
    *,
    session_id: str = "s1",
    proposed_scope: str = "session:s1",
    status: str = "proposed",
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        session_id=session_id,
        proposed_scope=proposed_scope,
        type="fact",
        content={"text": f"candidate {candidate_id}"},
        status=status,
    )


@pytest.fixture(params=["sqlite", "mock"], ids=["sqlite", "mock"])
def store(request: pytest.FixtureRequest):
    with tempfile.TemporaryDirectory() as tmp:
        if request.param == "sqlite":
            yield SQLiteMemoryStore(Path(tmp) / "memory.db")
        else:
            yield InMemoryMemoryStore()


def test_characterization_record_crud_and_listing(store) -> None:
    first = _record("r1", title="alpha title", tags=["alpha"], entities=["Orion"])
    second = _record(
        "r2",
        scope="session:s2",
        record_type="task",
        content={"text": "beta memory"},
        title="beta title",
    )
    store.put(first)
    store.put(second)

    fetched = store.get("r1")
    assert fetched is not None
    assert fetched.id == "r1"
    assert fetched.title == "alpha title"

    listed = store.list(
        ListQueryOptions(
            scopes=["session:s1", "session:s2"],
            order_by=RecordOrder.UPDATED_AT_DESC,
        )
    )
    assert {item.id for item in listed} == {"r1", "r2"}

    scopes = store.list_scopes()
    assert "session:s1" in scopes
    assert "session:s2" in scopes

    store.delete("r2")
    post_delete = store.list(ListQueryOptions(scopes=["session:s1", "session:s2"]))
    assert {item.id for item in post_delete} == {"r1"}


def test_characterization_search_feedback_entities_and_touch(store) -> None:
    record = _record(
        "r1",
        title="mdc generalization",
        content="mdc generalization e2e is active",
        tags=["mdc"],
        entities=["Alice"],
    )
    store.put(record)

    search_hits = store.search(
        SearchQueryOptions(
            query="mdc generalization",
            scopes=["session:s1"],
        )
    )
    assert any(item.id == "r1" for item in search_hits)

    entity_hits = store.retrieve_by_entities(["Alice"], ["session:s1"])
    assert [item.id for item in entity_hits] == ["r1"]

    store.touch_last_hit("r1")
    touched = store.get("r1")
    assert touched is not None
    assert touched.last_hit_at
    assert touched.access_count == 1

    updated = store.apply_outcome_feedback(
        ["r1", "r1"],
        outcome="success",
        command_id="cmd-1",
        observed_at=_now(),
        feedback_delta=0.2,
    )
    assert updated == 1
    refreshed = store.get("r1")
    assert refreshed is not None
    assert float(refreshed.meta["feedback_score"]) > 0.0


def test_characterization_tier_filters_and_transitions(store) -> None:
    record = _record("r-tier", title="tiered")
    store.put(record)

    initial = store.list(ListQueryOptions(scopes=["session:s1"], tiers=["working"]))
    assert [item.id for item in initial] == ["r-tier"]

    transitioned = store.transition_tier(
        "r-tier",
        to_tier="archival",
        transition_reason="manual_override",
        transition_at=_now(),
        meta={"source": "test"},
    )
    assert transitioned.from_tier == "working"
    assert transitioned.to_tier == "archival"

    archival = store.list(ListQueryOptions(scopes=["session:s1"], tiers=["archival"]))
    assert [item.id for item in archival] == ["r-tier"]

    transitions = store.list_tier_transitions(record_id="r-tier")
    assert len(transitions) == 1
    assert transitions[0].transition_reason == "manual_override"


def test_characterization_upsert_history_tombstone_and_supersession(store) -> None:
    first = store.upsert(
        "session:s1",
        "fact",
        "theme",
        {"content": {"color": "red"}, "title": "Theme"},
    )
    second = store.upsert(
        "session:s1",
        "fact",
        "theme",
        {"content": {"color": "blue"}},
    )
    assert second.supersedes_id == first.id

    history = store.history("session:s1", "fact", "theme")
    assert {item.id for item in history[:2]} == {second.id, first.id}

    third = store.upsert(
        "session:s1",
        "fact",
        "topic",
        {"content": "alpha"},
    )
    store.tombstone("session:s1", "fact", "topic")
    tombstoned = store.get(third.id)
    assert tombstoned is not None
    assert tombstoned.is_deleted

    contradicted_old = store.upsert(
        "agent:main",
        "fact",
        "policy",
        {"content": "old policy"},
    )
    contradicted_new = store.upsert(
        "agent:main",
        "fact",
        "policy-next",
        {"content": "new policy"},
    )
    result = store.supersede_by_contradiction(contradicted_old.id, contradicted_new.id)
    assert result.id == contradicted_new.id
    assert result.key == "policy"


def test_characterization_candidate_lifecycle_and_promotion(store) -> None:
    candidate = _candidate("c1")
    store.candidate_put(candidate)
    fetched = store.candidate_get("c1")
    assert fetched is not None
    assert fetched.status == "proposed"

    reviewed = store.candidate_update(
        "c1",
        {
            "status": "approved",
            "review": CandidateReview(
                reviewer="agent",
                decided_at="2026-04-01T00:00:00+00:00",
                note="looks good",
            ),
        },
    )
    assert reviewed.status == "approved"
    listed = store.candidate_list(
        CandidateListOptions(session_id="s1", status="approved")
    )
    assert [item.candidate_id for item in listed] == ["c1"]

    promoted = store.promote_candidate("c1", "agent:main")
    assert promoted.scope == "agent:main"
    assert promoted.id
