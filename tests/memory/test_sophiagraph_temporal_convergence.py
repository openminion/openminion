from __future__ import annotations

import pytest

from openminion.modules.memory.submissions import (
    SubmissionEnvelope,
    SubmissionNamespace,
    SubmissionProvenance,
    reset_idempotency_registry,
    submit_envelope,
)
from sophiagraph import SophiaGraphMemoryStore


@pytest.fixture(autouse=True)
def _reset_idempotency():
    reset_idempotency_registry()
    yield
    reset_idempotency_registry()


@pytest.fixture
def store() -> SophiaGraphMemoryStore:
    return SophiaGraphMemoryStore()


def _ns() -> SubmissionNamespace:
    return SubmissionNamespace(tenant_id="acme", agent_id="alpha", session_id="sess-1")


def _prov(turn: str = "turn-1") -> SubmissionProvenance:
    return SubmissionProvenance(source_owner="task-runner", turn_id=turn)


# Helpers


def _submit_raw_episode(store, episode_id: str = "ep-r1") -> None:
    from sophiagraph.models import MemoryNamespace, RawEpisode
    from sophiagraph.models.entity_fact import EntityFactProvenance

    store.put_raw_episode(
        RawEpisode(
            episode_id=episode_id,
            kind="user_input",
            source="chat",
            source_id=f"msg-{episode_id}",
            namespace=MemoryNamespace(agent_id="alpha", session_id="sess-1"),
            occurred_at="2026-05-29T10:00:00",
            ingested_at="2026-05-29T10:00:01",
            payload={"text": "(caller-supplied raw payload)"},
            provenance=EntityFactProvenance(
                source_kind="user_input",
                source_id=episode_id,
                actor="user",
            ),
        )
    )


# SGTKG-06 — end-to-end flow


def test_openminion_submits_episode_and_fact_then_retrieves_active(store) -> None:
    # Step 1: persist a RawEpisode (direct package call — OMSS does not
    # yet ship a raw_episode payload kind by design; SGTKG owns that
    # convergence surface separately).
    _submit_raw_episode(store, "ep-r1")
    assert store.get_raw_episode("ep-r1") is not None

    # Step 2: submit a model-authored fact candidate via OMSS that
    # references the raw episode in source_episode_ids.
    fact_result = submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="fact_candidate",
            payload={
                "fact_id": "f-role-1",
                "subject_entity_id": "ent-alice",
                "predicate": "role",
                "object_literal": "manager",
                "observed_at": "2026-05-29T10:00:00",
                "source_episode_ids": ["ep-r1"],
            },
            provenance=_prov("t-1"),
            idempotency_key="idem-fact-1",
            trust_mode="direct",
        ),
    )
    assert fact_result.ok
    fact = store.get_fact("f-role-1")
    assert fact is not None
    assert "ep-r1" in fact.source_episode_ids

    # The fact appears under the source_episode_id filter.
    by_episode = store.list_facts(source_episode_id="ep-r1")
    assert [f.fact_id for f in by_episode] == ["f-role-1"]


def test_openminion_invalidates_fact_via_contradiction_decision(store) -> None:
    _submit_raw_episode(store, "ep-r1")
    _submit_raw_episode(store, "ep-r2")
    # Two competing facts, both referencing distinct raw episodes.
    submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="fact_candidate",
            payload={
                "fact_id": "f-old",
                "subject_entity_id": "ent-alice",
                "predicate": "role",
                "object_literal": "manager",
                "observed_at": "2026-05-29T10:00:00",
                "source_episode_ids": ["ep-r1"],
            },
            provenance=_prov("t-1"),
            idempotency_key="idem-fact-old",
            trust_mode="direct",
        ),
    )
    submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="fact_candidate",
            payload={
                "fact_id": "f-new",
                "subject_entity_id": "ent-alice",
                "predicate": "role",
                "object_literal": "ic",
                "observed_at": "2026-06-01T00:00:00",
                "source_episode_ids": ["ep-r2"],
            },
            provenance=_prov("t-2"),
            idempotency_key="idem-fact-new",
            trust_mode="direct",
        ),
    )
    submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="contradiction_decision",
            payload={
                "contradiction_id": "c-1",
                "target_fact_id": "f-old",
                "contradicting_fact_id": "f-new",
                "decision": "supersedes",
                "decided_at": "2026-06-01T00:00:00",
            },
            provenance=_prov("t-3"),
            idempotency_key="idem-c-1",
            trust_mode="direct",
        ),
    )
    active = store.list_facts(active_state="active")
    historical = store.list_facts(active_state="historical")
    assert {f.fact_id for f in active} == {"f-new"}
    assert [f.fact_id for f in historical] == ["f-old"]
    target = store.get_fact("f-old")
    assert target is not None
    assert target.superseded_by_fact_id == "f-new"


def test_point_in_time_query_returns_historical_with_provenance(store) -> None:
    _submit_raw_episode(store, "ep-r1")
    submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="fact_candidate",
            payload={
                "fact_id": "f-1",
                "subject_entity_id": "ent-alice",
                "predicate": "lives_in",
                "object_literal": "Berlin",
                "valid_from": "2020-01-01",
                "valid_to": "2022-12-31",
                "observed_at": "2020-01-01",
                "source_episode_ids": ["ep-r1"],
            },
            provenance=_prov("t-1"),
            idempotency_key="idem-fact-2020",
            trust_mode="direct",
        ),
    )
    # Point-in-time: what did we know about 2021?
    in_2021 = store.list_facts(valid_at="2021-06-01", active_state="all")
    assert [f.fact_id for f in in_2021] == ["f-1"]
    # And provenance survives — source_episode_ids are intact.
    assert in_2021[0].source_episode_ids == ["ep-r1"]


def test_sophiagraph_has_no_openminion_imports() -> None:
    import inspect

    import sophiagraph
    import sophiagraph.models
    import sophiagraph.models.convergence
    import sophiagraph.models.entity_fact
    import sophiagraph.storage.base
    import sophiagraph.storage.entity_episode_store

    for module in (
        sophiagraph,
        sophiagraph.models,
        sophiagraph.models.convergence,
        sophiagraph.models.entity_fact,
        sophiagraph.storage.base,
        sophiagraph.storage.entity_episode_store,
    ):
        source = inspect.getsource(module)
        assert "openminion" not in source, (
            f"{module.__name__} must not reference openminion; SGTKG-06 import boundary"
        )
