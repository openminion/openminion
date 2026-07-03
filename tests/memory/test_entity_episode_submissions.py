from __future__ import annotations

from typing import Any

import pytest

from openminion.modules.memory.submissions import (
    PAYLOAD_KINDS,
    SubmissionEnvelope,
    SubmissionEnvelopeError,
    SubmissionNamespace,
    SubmissionProvenance,
    emit_decision,
    emit_entity_candidate,
    emit_episode_event,
    emit_episode_step,
    emit_fact_candidate,
    emit_procedure,
    reset_idempotency_registry,
    submit_envelope,
)
from sophiagraph import SophiaGraphMemoryStore


@pytest.fixture(autouse=True)
def _reset():
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


def test_new_payload_kinds_registered() -> None:
    for kind in (
        "entity_candidate",
        "entity_alias_candidate",
        "fact_candidate",
        "contradiction_decision",
        "entity_summary",
        "episode_event",
        "episode_step",
        "decision",
        "procedure",
    ):
        assert kind in PAYLOAD_KINDS


def test_entity_candidate_direct_writes_entity(store) -> None:
    result = emit_entity_candidate(
        store,
        namespace=_ns(),
        turn_id="t-1",
        payload={"entity_id": "ent-1", "canonical_name": "Alice"},
        source_owner="agent",
        idempotency_key="idem-ent-1",
        trust_mode="direct",
    )
    assert result.ok and result.object_id == "ent-1"
    assert store.get_entity("ent-1").canonical_name == "Alice"


def test_entity_candidate_candidate_mode_stages_to_candidate_store(store) -> None:
    result = emit_entity_candidate(
        store,
        namespace=_ns(),
        turn_id="t-1",
        payload={"canonical_name": "Bob"},
        source_owner="agent",
        idempotency_key="idem-ent-cand-1",
    )
    assert result.ok
    assert store.list_entities() == []
    assert store.get_candidate(result.object_id) is not None


def test_fact_candidate_direct_writes_fact_with_provenance(store) -> None:
    store.put_entity_summary  # ensure attr exists  # noqa: B018
    result = emit_fact_candidate(
        store,
        namespace=_ns(),
        turn_id="t-1",
        tool_call_id="tc-1",
        payload={
            "fact_id": "f-1",
            "subject_entity_id": "ent-1",
            "predicate": "lives_in",
            "object_literal": "Berlin",
            "observed_at": "2026-05-29",
        },
        source_owner="agent",
        idempotency_key="idem-fact-1",
        trust_mode="direct",
    )
    assert result.ok
    fact = store.get_fact("f-1")
    assert fact is not None
    assert fact.predicate == "lives_in"
    assert fact.provenance is not None
    assert fact.provenance.actor == "agent"


def test_contradiction_decision_invalidates_target(store) -> None:
    for fid, lit in (("f-a", "manager"), ("f-b", "ic")):
        envelope = SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="fact_candidate",
            payload={
                "fact_id": fid,
                "subject_entity_id": "ent-1",
                "predicate": "role",
                "object_literal": lit,
                "observed_at": "2026-05-29",
            },
            provenance=_prov(),
            idempotency_key=f"idem-{fid}",
            trust_mode="direct",
        )
        submit_envelope(store, envelope)
    result = submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="contradiction_decision",
            payload={
                "contradiction_id": "c-1",
                "target_fact_id": "f-a",
                "contradicting_fact_id": "f-b",
                "decision": "invalidates_target",
                "decided_at": "2026-05-29",
            },
            provenance=_prov(),
            idempotency_key="idem-c-1",
            trust_mode="direct",
        ),
    )
    assert result.ok
    target = store.get_fact("f-a")
    other = store.get_fact("f-b")
    assert target.is_invalidated is True
    assert other.is_invalidated is False


def test_entity_summary_direct_persists_typed_record(store) -> None:
    result = submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="entity_summary",
            payload={
                "summary_id": "s-1",
                "entity_id": "ent-1",
                "summary_text": "Alice is the on-call engineer (model-authored).",
            },
            provenance=_prov(),
            idempotency_key="idem-sum-1",
            trust_mode="direct",
        ),
    )
    assert result.ok
    rows = store.list_entity_summaries(entity_id="ent-1")
    assert len(rows) == 1


def test_episode_event_direct_writes_episode(store) -> None:
    result = emit_episode_event(
        store,
        namespace=_ns(),
        turn_id="t-1",
        payload={
            "episode_id": "ep-1",
            "title": "Build feature X",
            "status": "in_progress",
            "started_at": "2026-05-29T10:00:00",
        },
        source_owner="agent",
        idempotency_key="idem-ep-1",
    )
    assert result.ok
    ep = store.get_episode("ep-1")
    assert ep is not None and ep.title == "Build feature X"


def test_episode_step_with_tool_call_provenance(store) -> None:
    result = emit_episode_step(
        store,
        namespace=_ns(),
        turn_id="t-1",
        tool_call_id="tc-2",
        payload={
            "step_id": "step-1",
            "episode_id": "ep-1",
            "kind": "tool_call",
            "sequence": 0,
            "occurred_at": "2026-05-29T10:01:00",
            "tool_id": "bash",
        },
        source_owner="execution",
        idempotency_key="idem-step-1",
    )
    assert result.ok
    steps = store.list_episode_steps(episode_id="ep-1")
    assert len(steps) == 1
    assert steps[0].tool_id == "bash"


def test_decision_emitter(store) -> None:
    result = emit_decision(
        store,
        namespace=_ns(),
        turn_id="t-1",
        payload={
            "decision_id": "d-1",
            "title": "Pick stack",
            "chosen": "python",
            "alternatives": ["go", "rust"],
            "occurred_at": "2026-05-29T10:02:00",
            "episode_id": "ep-1",
        },
        source_owner="agent",
        idempotency_key="idem-d-1",
    )
    assert result.ok
    assert store.list_decisions(episode_id="ep-1")[0].chosen == "python"


def test_procedure_emitter_default_experimental(store) -> None:
    result = emit_procedure(
        store,
        namespace=_ns(),
        payload={
            "procedure_id": "p-1",
            "title": "Run focused tests",
            "steps": [
                {"sequence": 0, "title": "cd module"},
                {"sequence": 1, "title": "run pytest", "tool_id": "bash"},
            ],
            "source_episode_ids": ["ep-1"],
        },
        source_owner="agent",
        idempotency_key="idem-p-1",
    )
    assert result.ok
    proc = store.get_procedure("p-1")
    assert proc is not None
    assert proc.promotion_tier == "experimental"
    assert len(proc.steps) == 2


class _BrokenStore:
    def __getattr__(self, name: str) -> Any:
        def _boom(*_a: Any, **_kw: Any) -> Any:
            raise RuntimeError(f"boom from {name}")

        return _boom


def test_failure_non_blocking_for_new_kinds() -> None:
    broken = _BrokenStore()
    result = emit_episode_event(
        broken,
        namespace=_ns(),
        turn_id="t-1",
        payload={
            "title": "X",
            "status": "in_progress",
            "started_at": "2026-05-29T10:00:00",
        },
        source_owner="agent",
        idempotency_key="idem-fail-ep",
    )
    assert result.ok is False
    assert result.code == "BACKEND_FAILURE"
    assert result.error_type == "RuntimeError"


def test_envelope_rejects_unknown_kind_still() -> None:
    with pytest.raises(SubmissionEnvelopeError):
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="invent_a_thing",
            payload={},
            provenance=_prov(),
            idempotency_key="idem-bad",
        )
