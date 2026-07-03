from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from openminion.modules.memory.submissions import (
    SubmissionEnvelope,
    SubmissionNamespace,
    SubmissionProvenance,
    emit_retrieval_feedback,
    emit_user_correction,
    emit_validation_outcome,
    reset_idempotency_registry,
    submit_envelope,
)
from sophiagraph import (
    LifecyclePolicy,
    PromotionPredicate,
    PromotionPredicateKind,
    SophiaGraphMemoryStore,
    apply_decision_to_record_meta,
    evaluate_policy,
)
from sophiagraph.audit import (
    LifecycleActionEvent,
    MemoryAuditEvent,
    PolicyDecision,
    PolicyDenialEvent,
    PolicyRequest,
    QualityEvalSignal,
    RetrievalEvent,
    WriteAcceptedEvent,
    WriteAttemptEvent,
    build_policy_denial_event,
    evaluate_policy_hooks,
)
from sophiagraph.models import MemoryNamespace


@pytest.fixture(autouse=True)
def _reset():
    reset_idempotency_registry()
    yield
    reset_idempotency_registry()


@pytest.fixture
def store() -> SophiaGraphMemoryStore:
    return SophiaGraphMemoryStore()


def _ns() -> SubmissionNamespace:
    return SubmissionNamespace(agent_id="alpha", session_id="sess-1")


def _sg_ns() -> MemoryNamespace:
    return MemoryNamespace(agent_id="alpha", session_id="sess-1")


def _prov() -> SubmissionProvenance:
    return SubmissionProvenance(source_owner="task-runner", turn_id="t-1")


def test_emit_validation_outcome_still_routes_through_public_envelope(store) -> None:
    result = emit_validation_outcome(
        store,
        namespace=_ns(),
        validation_command="ruff",
        payload={
            "id": "outcome-rec-1",
            "content": "ruff passed",
            "type": "fact",
            "meta": {"status": "passed"},
        },
        source_owner="ci-runner",
        idempotency_key="idem-val-1",
    )
    assert result.ok
    assert store.get_record("outcome-rec-1") is not None


def test_emit_user_correction_routes_through_public_envelope(store) -> None:
    result = emit_user_correction(
        store,
        namespace=_ns(),
        user_correction_id="uc-1",
        payload={
            "candidate_id": "cand-uc-1",
            "content": "user-supplied correction",
            "type": "fact",
            "confidence": 0.9,
        },
        source_owner="user",
        idempotency_key="idem-uc-1",
        trust_mode="candidate",
    )
    assert result.ok


def test_emit_retrieval_feedback_routes_through_public_envelope(store) -> None:
    result = emit_retrieval_feedback(
        store,
        namespace=_ns(),
        turn_id="t-1",
        payload={
            "id": "rec-feedback-1",
            "content": "feedback body",
            "type": "fact",
            "meta": {"signal": "retrieval_used"},
        },
        source_owner="task-runner",
        idempotency_key="idem-fb-1",
    )
    assert result.ok


def test_openminion_policy_denial_flow_uses_public_sophiagraph_types(store) -> None:
    audit_log: list[MemoryAuditEvent] = []

    def recorder(event: MemoryAuditEvent) -> None:
        audit_log.append(event)

    def hook(req: PolicyRequest) -> PolicyDecision:
        if not req.payload_meta.get("idempotency_key"):
            return PolicyDecision(
                action="deny",
                policy_id="require-idempotency",
                reason_code="POLICY_REQUIRED_FIELDS_MISSING",
            )
        return PolicyDecision(action="allow", policy_id="ok")

    write_attempt = WriteAttemptEvent(
        namespace=_sg_ns(),
        payload_kind="document",
        source_owner="task-runner",
        target_kind="record",
        target_id="rec-X",
        idempotency_key=None,
        trust_mode="direct",
    )
    recorder(write_attempt.to_memory_audit_event())

    request = PolicyRequest(
        namespace=_sg_ns(),
        surface="write",
        source_owner="task-runner",
        target_kind="record",
        target_id="rec-X",
        payload_kind="document",
        payload_meta={"idempotency_key": ""},
    )
    decision = evaluate_policy_hooks(request, [hook])
    assert decision.denied
    assert decision.reason_code == "POLICY_REQUIRED_FIELDS_MISSING"

    denial = build_policy_denial_event(request, decision)
    assert isinstance(denial, PolicyDenialEvent)
    recorder(denial.to_memory_audit_event())

    assert store.get_record("rec-X") is None

    assert len(audit_log) == 2
    assert audit_log[0].event_type == "memory.write_attempt"
    assert audit_log[1].event_type == "memory.policy_denial"
    assert audit_log[1].details["reason_code"] == "POLICY_REQUIRED_FIELDS_MISSING"


def test_openminion_policy_allow_path_proceeds_to_submit_envelope(store) -> None:
    def hook(req: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(action="allow", policy_id="ok")

    request = PolicyRequest(
        namespace=_sg_ns(),
        surface="write",
        source_owner="task-runner",
        target_kind="record",
        target_id="rec-allowed",
        payload_kind="document",
        payload_meta={"idempotency_key": "idem-allow"},
    )
    decision = evaluate_policy_hooks(request, [hook])
    assert decision.allowed

    result = submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="document",
            payload={
                "id": "rec-allowed",
                "content": "x",
                "type": "fact",
                "meta": {},
            },
            provenance=_prov(),
            idempotency_key="idem-allow",
            trust_mode="direct",
        ),
    )
    assert result.ok

    accepted = WriteAcceptedEvent(
        namespace=_sg_ns(),
        payload_kind="document",
        source_owner="task-runner",
        target_kind="record",
        target_id="rec-allowed",
    )
    audit = accepted.to_memory_audit_event()
    assert audit.event_type == "memory.write_accepted"


def test_openminion_retrieval_selection_records_typed_evidence(store) -> None:
    audit_log: list[MemoryAuditEvent] = []

    def recorder(event: MemoryAuditEvent) -> None:
        audit_log.append(event)

    retrieval = RetrievalEvent(
        namespace=_sg_ns(),
        source_owner="task-runner",
        selected_ids=("rec-1", "rec-2"),
        omitted_count=4,
        retrieval_mode="local_graph",
        session_id="sess-1",
    )
    recorder(retrieval.to_memory_audit_event())

    used = QualityEvalSignal(
        namespace=_sg_ns(),
        record_id="rec-1",
        signal_kind="retrieval_used",
        source_owner="task-runner",
        session_id="sess-1",
        details={"turn_id": "t-1"},
    )
    ignored = QualityEvalSignal(
        namespace=_sg_ns(),
        record_id="rec-2",
        signal_kind="retrieval_ignored",
        source_owner="task-runner",
        session_id="sess-1",
    )
    recorder(used.to_memory_audit_event())
    recorder(ignored.to_memory_audit_event())

    assert [e.event_type for e in audit_log] == [
        "memory.retrieval",
        "memory.quality_eval_signal",
        "memory.quality_eval_signal",
    ]
    assert audit_log[0].details["selected_ids"] == ["rec-1", "rec-2"]
    assert audit_log[1].details["signal_kind"] == "retrieval_used"
    assert audit_log[2].details["signal_kind"] == "retrieval_ignored"


NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_openminion_lifecycle_evaluate_then_apply_via_public_api(store) -> None:
    submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="document",
            payload={
                "id": "rec-lc-1",
                "content": "x",
                "type": "fact",
                "meta": {"access_count_estimate": 0},
            },
            provenance=_prov(),
            idempotency_key="idem-lc-1",
            trust_mode="direct",
        ),
    )
    policy = LifecyclePolicy(
        policy_id="p-openminion-1",
        namespace_filter=_sg_ns(),
        created_at_iso=_iso(NOW),
        ttl_active_iso="P30D",
        ttl_cooling_iso="P7D",
        promotion_predicates=(
            PromotionPredicate(
                kind=PromotionPredicateKind.ACCESS_COUNT_ABOVE_THRESHOLD,
                threshold=3,
            ),
        ),
    )
    store.put_lifecycle_policy(policy)
    assert store.get_lifecycle_policy("p-openminion-1") == policy

    record = store.get_record("rec-lc-1")
    assert record is not None
    from dataclasses import replace

    aged = replace(record, updated_at=_iso(NOW - timedelta(days=40)))
    decision = evaluate_policy(aged, policy, _iso(NOW))
    assert decision.transition_reason == "ttl_active_elapsed"
    assert decision.next_phase.value == "cooling"

    new_meta = apply_decision_to_record_meta(aged.meta, decision)
    assert new_meta["lifecycle_phase"] == "cooling"
    second = apply_decision_to_record_meta(new_meta, decision)
    assert second == new_meta


def test_openminion_lifecycle_promotion_path_via_access_signal(store) -> None:
    submit_envelope(
        store,
        SubmissionEnvelope(
            namespace=_ns(),
            payload_kind="document",
            payload={
                "id": "rec-lc-2",
                "content": "x",
                "type": "fact",
                "meta": {"lifecycle_phase": "cooling"},
            },
            provenance=_prov(),
            idempotency_key="idem-lc-2",
            trust_mode="direct",
        ),
    )
    record = store.get_record("rec-lc-2")
    assert record is not None
    from dataclasses import replace

    accessed = replace(
        record,
        updated_at=_iso(NOW - timedelta(days=32)),
        access_count=10,
    )

    policy = LifecyclePolicy(
        policy_id="p-promote-1",
        namespace_filter=_sg_ns(),
        created_at_iso=_iso(NOW),
        ttl_active_iso="P30D",
        ttl_cooling_iso="P30D",
        promotion_predicates=(
            PromotionPredicate(
                kind=PromotionPredicateKind.ACCESS_COUNT_ABOVE_THRESHOLD,
                threshold=5,
            ),
        ),
    )
    store.put_lifecycle_policy(policy)

    decision = evaluate_policy(accessed, policy, _iso(NOW))
    assert decision.transition_reason == "promotion_predicate_matched"
    assert decision.next_phase.value == "active"

    action_event = LifecycleActionEvent(
        namespace=_sg_ns(),
        record_id="rec-lc-2",
        action="promotion_applied",
        policy_id="p-promote-1",
        from_phase="cooling",
        to_phase="active",
        job_id="job-1",
    )
    audit = action_event.to_memory_audit_event()
    assert audit.event_type == "memory.lifecycle_action"
    assert audit.details["action"] == "promotion_applied"


def test_no_prose_inference_helpers_on_governance_or_lifecycle_surface() -> None:
    from openminion.modules.memory import submissions as mod

    forbidden = {
        "infer_policy_from_prose",
        "guess_lifecycle_decision",
        "auto_promote_from_content",
        "classify_quality_from_text",
    }
    assert set(mod.__all__) & forbidden == set()


def test_governance_test_file_imports_only_public_sophiagraph_paths() -> None:
    import ast

    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_modules = {
        "sophiagraph.storage.memory",
        "sophiagraph.storage.sqlite",
        "sophiagraph.storage.lifecycle_policy",
        "sophiagraph.audit.events",
        "sophiagraph.audit.governance",
        "sophiagraph.audit.policy",
    }
    actual_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            actual_imports.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                actual_imports.add(alias.name)
    leaked = actual_imports & forbidden_modules
    assert not leaked, (
        f"test file reaches into non-public sophiagraph paths: {sorted(leaked)}"
    )
