from __future__ import annotations

import pytest

from openminion.modules.memory.models import (
    MemoryCandidate,
)
from openminion.modules.memory.runtime.promotion import PromotionPolicy
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.storage.base import SearchQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from sophiagraph.contracts.errors import InvalidArgumentError
from sophiagraph.models.core import _assert_scope


def test_project_scope_validation_is_additive() -> None:
    _assert_scope("project:my-proj-123")
    with pytest.raises(InvalidArgumentError):
        _assert_scope("project:")
    with pytest.raises(InvalidArgumentError):
        _assert_scope("invalid:proj")


def test_project_scope_promotion_policy_respects_trust_boundary() -> None:
    policy = PromotionPolicy(auto_promote_sources={"validated"})

    validated = MemoryCandidate(
        candidate_id="cand-validated",
        session_id="s1",
        proposed_scope="agent:a1",
        type="fact",
        content="validated fact",
        source="validated",
        status="proposed",
    )
    user_said = MemoryCandidate(
        candidate_id="cand-user",
        session_id="s1",
        proposed_scope="agent:a1",
        type="fact",
        content="user fact",
        source="user_said",
        status="proposed",
    )
    agent_inferred = MemoryCandidate(
        candidate_id="cand-inferred",
        session_id="s1",
        proposed_scope="agent:a1",
        type="fact",
        content="agent fact",
        source="agent_inferred",
        status="proposed",
    )
    approved = MemoryCandidate(
        candidate_id="cand-approved",
        session_id="s1",
        proposed_scope="agent:a1",
        type="fact",
        content="approved fact",
        source="agent_inferred",
        status="approved",
    )

    assert policy.evaluate(validated, "project:proj-1").allowed is True
    assert policy.evaluate(user_said, "project:proj-1").allowed is True
    assert policy.evaluate(agent_inferred, "project:proj-1").allowed is False
    assert policy.evaluate(approved, "project:proj-1").allowed is True


def test_search_all_queries_all_scopes_and_filters_confidence() -> None:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    service.write_record(
        scope="agent:a1",
        record_type="fact",
        title="Agent weather",
        content="weather agent note",
        tags=["weather"],
    )
    service._store.upsert(  # noqa: SLF001
        "project:p1",
        "fact",
        "project-weather",
        {
            "title": "Project weather",
            "content": "weather project note",
            "confidence": 0.9,
        },
    )
    service._store.upsert(  # noqa: SLF001
        "global:system",
        "fact",
        "global-weather",
        {
            "title": "Global weather",
            "content": "weather global note",
            "confidence": 0.2,
        },
    )

    all_hits = service.search_all("weather", scopes=None, limit=10)
    project_hits = service.search_all("weather", scopes=["project:p1"], limit=10)
    filtered_hits = service.search_all(
        "weather", scopes=None, min_confidence=0.8, limit=10
    )

    assert {record.scope for record in all_hits} == {
        "agent:a1",
        "project:p1",
        "global:system",
    }
    assert [record.scope for record in project_hits] == ["project:p1"]
    assert {record.scope for record in filtered_hits} == {"project:p1"}


def test_search_all_empty_query_is_safe() -> None:
    service = MemoryService(store=InMemoryMemoryStore())
    assert service.search_all("", scopes=None, limit=10) == []


def test_sqlite_search_falls_back_to_sanitized_query_when_raw_query_misses(
    tmp_path,
) -> None:
    store = SQLiteMemoryStore(tmp_path / "mii-phase1.db")
    service = MemoryService(store=store)
    service.write_record(
        scope="agent:a1",
        record_type="fact",
        title="Shell preference",
        content="my favorite shell is zsh",
        tags=["shell"],
    )

    hits = service.search(
        SearchQueryOptions(
            query="what shell do i prefer?",
            scopes=["agent:a1"],
            limit=10,
        )
    )

    assert hits
    assert any("zsh" in str(record.content) for record in hits)
