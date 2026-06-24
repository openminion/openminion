from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from typing import get_args

import pytest

from openminion.modules.runtime.audit import (
    AUDIT_EVENT_KINDS,
    AUDIT_EVENT_RECORDED_EVENT_TYPE,
    AUDIT_RUNTIME_SOURCES,
    DEFAULT_AUDIT_RETENTION_POLICY,
    GDPR_ERASURE_ACCESS_KINDS,
    HIPAA_SENSITIVE_ACCESS_KINDS,
    SOC2_CHANGE_DECISION_KINDS,
    AuditAppendOnlyViolation,
    AuditEvent,
    AuditEventKind,
    AuditQueryRequest,
    AuditQueryResult,
    AuditRetentionPolicy,
    AuditRuntimeSource,
    FixedClock,
    InMemoryAuditLog,
    apply_audit_retention_policy,
    audit_event_kind_for_source,
    gdpr_erasure_access_query,
    hipaa_sensitive_access_query,
    project_runtime_event_to_audit_event,
    query_audit_events,
    record_audit_event,
    soc2_change_decision_query,
)
from openminion.modules.runtime.constants import AUDIT_EVENT_TYPE_PREFIX


def test_audit_event_kind_literal_is_exhaustive_eight_values() -> None:
    assert set(get_args(AuditEventKind)) == {
        "tool_invoked",
        "memory_read",
        "memory_mutated",
        "credential_access",
        "policy_decision",
        "intervention_issued",
        "user_data_exported",
        "user_data_erased",
    }
    assert len(get_args(AuditEventKind)) == 8


def test_audit_event_kinds_tuple_matches_literal() -> None:
    assert tuple(get_args(AuditEventKind)) == AUDIT_EVENT_KINDS


def test_audit_runtime_sources_literal_is_closed_set() -> None:
    assert set(get_args(AuditRuntimeSource)) == set(AUDIT_RUNTIME_SOURCES)
    assert len(get_args(AuditRuntimeSource)) == 8


def test_audit_event_type_prefix_is_canonical_string() -> None:
    assert AUDIT_EVENT_TYPE_PREFIX == "audit."
    assert AUDIT_EVENT_RECORDED_EVENT_TYPE.startswith(AUDIT_EVENT_TYPE_PREFIX)


def _make_event(
    *,
    kind: AuditEventKind = "tool_invoked",
    actor_ref: str = "actor-1",
    target_ref: str = "tool:shell",
    timestamp: datetime | None = None,
    trace_id: str = "trace-1",
    session_id: str = "session-1",
    policy_ref: str = "policy-default",
    artifact_refs: tuple[str, ...] = (),
    redaction_mode: str = "none",
    immutable: bool = True,
) -> AuditEvent:
    return AuditEvent(
        kind=kind,
        actor_ref=actor_ref,
        target_ref=target_ref,
        timestamp=timestamp or datetime(2026, 5, 13, 12, tzinfo=timezone.utc),
        trace_id=trace_id,
        session_id=session_id,
        policy_ref=policy_ref,
        artifact_refs=artifact_refs,
        redaction_mode=redaction_mode,
        immutable=immutable,
    )


def test_audit_event_has_exactly_ten_fields() -> None:
    expected = {
        "kind",
        "actor_ref",
        "target_ref",
        "timestamp",
        "trace_id",
        "session_id",
        "policy_ref",
        "artifact_refs",
        "redaction_mode",
        "immutable",
    }
    assert set(AuditEvent.__dataclass_fields__.keys()) == expected
    assert len(AuditEvent.__dataclass_fields__) == 10


def test_audit_event_is_frozen_dataclass() -> None:
    event = _make_event()
    with pytest.raises(FrozenInstanceError):
        event.kind = "memory_read"  # type: ignore[misc]


_SOURCE_KIND_PAIRS: list[tuple[AuditRuntimeSource, AuditEventKind]] = [
    ("canonical_tool_event", "tool_invoked"),
    ("gws_credential_event", "credential_access"),
    ("memory_context_event", "memory_read"),
    ("memory_writer_event", "memory_mutated"),
    ("executor_security_event", "policy_decision"),
    ("intervention_recorded_event", "intervention_issued"),
    ("memory_export_event", "user_data_exported"),
    ("memory_erase_event", "user_data_erased"),
]


@pytest.mark.parametrize("source,expected_kind", _SOURCE_KIND_PAIRS)
def test_projection_maps_each_runtime_source_to_one_kind(
    source: AuditRuntimeSource, expected_kind: AuditEventKind
) -> None:
    event = project_runtime_event_to_audit_event(
        source,
        actor_ref="actor-1",
        target_ref="resource-1",
        timestamp=datetime(2026, 5, 13, tzinfo=timezone.utc),
        trace_id="trace-1",
        session_id="session-1",
        policy_ref="policy-1",
    )
    assert event.kind == expected_kind
    assert audit_event_kind_for_source(source) == expected_kind


def test_projection_is_deterministic_same_inputs_same_output() -> None:
    args = dict(
        actor_ref="actor-1",
        target_ref="tool:shell",
        timestamp=datetime(2026, 5, 13, tzinfo=timezone.utc),
        trace_id="trace-1",
        session_id="session-1",
        policy_ref="policy-1",
    )
    a = project_runtime_event_to_audit_event("canonical_tool_event", **args)
    b = project_runtime_event_to_audit_event("canonical_tool_event", **args)
    assert a == b


def test_projection_rejects_unknown_source() -> None:
    with pytest.raises(ValueError):
        project_runtime_event_to_audit_event(
            "auto_detected_source",  # type: ignore[arg-type]
            actor_ref="a",
            target_ref="t",
            timestamp=datetime(2026, 5, 13, tzinfo=timezone.utc),
            trace_id="trace-1",
            session_id="session-1",
            policy_ref="policy-1",
        )


def test_source_to_kind_map_is_frozen_at_module_load() -> None:
    kinds = {audit_event_kind_for_source(s) for s in AUDIT_RUNTIME_SOURCES}
    assert kinds == set(AUDIT_EVENT_KINDS)


def test_record_audit_event_returns_emitter_assigned_id() -> None:
    log = InMemoryAuditLog()
    event_id = record_audit_event(_make_event(), audit_log=log)
    assert event_id.startswith("audit-")


def test_record_audit_event_appends_to_log_in_order() -> None:
    log = InMemoryAuditLog()
    e1 = _make_event(trace_id="trace-1")
    e2 = _make_event(trace_id="trace-2")
    id1 = record_audit_event(e1, audit_log=log)
    id2 = record_audit_event(e2, audit_log=log)
    pairs = list(log.iter_records())
    assert [pid for pid, _ in pairs] == [id1, id2]
    assert pairs[0][1] == e1
    assert pairs[1][1] == e2


def test_direct_delete_raises_append_only_violation() -> None:
    log = InMemoryAuditLog()
    event_id = record_audit_event(_make_event(), audit_log=log)
    with pytest.raises(AuditAppendOnlyViolation):
        log.delete(event_id)


def test_record_audit_event_rejects_unknown_kind() -> None:
    bogus = AuditEvent(
        kind="auto_detected_kind",  # type: ignore[arg-type]
        actor_ref="a",
        target_ref="t",
        timestamp=datetime(2026, 5, 13, tzinfo=timezone.utc),
        trace_id="trace-1",
        session_id="session-1",
        policy_ref="policy-1",
        artifact_refs=(),
        redaction_mode="none",
        immutable=True,
    )
    log = InMemoryAuditLog()
    with pytest.raises(ValueError):
        record_audit_event(bogus, audit_log=log)


def test_recorded_event_is_not_mutated_after_record() -> None:
    log = InMemoryAuditLog()
    original = _make_event()
    record_audit_event(original, audit_log=log)
    stored = list(log.iter_records())[0][1]
    assert stored == original
    with pytest.raises(FrozenInstanceError):
        stored.kind = "memory_read"  # type: ignore[misc]


def test_default_retention_policy_keys_are_closed_set() -> None:
    assert set(DEFAULT_AUDIT_RETENTION_POLICY.durations.keys()) == set(
        AUDIT_EVENT_KINDS
    )


def test_default_retention_policy_holds_intersect_no_erasure_eligible() -> None:
    assert DEFAULT_AUDIT_RETENTION_POLICY.holds.isdisjoint(
        DEFAULT_AUDIT_RETENTION_POLICY.erasure_eligible
    )


def test_retention_sweep_erases_only_eligible_expired_records() -> None:
    log = InMemoryAuditLog()
    now = datetime(2026, 5, 13, tzinfo=timezone.utc)
    long_ago = now - timedelta(days=365 * 10)
    record_audit_event(
        _make_event(kind="tool_invoked", timestamp=long_ago, trace_id="t-old"),
        audit_log=log,
    )
    record_audit_event(
        _make_event(kind="tool_invoked", timestamp=now, trace_id="t-new"),
        audit_log=log,
    )
    record_audit_event(
        _make_event(kind="policy_decision", timestamp=long_ago, trace_id="t-held"),
        audit_log=log,
    )

    result = apply_audit_retention_policy(
        DEFAULT_AUDIT_RETENTION_POLICY,
        audit_log=log,
        clock=FixedClock(now),
    )

    remaining = [event.trace_id for _id, event in log.iter_records()]
    assert "t-old" not in remaining
    assert "t-new" in remaining
    assert "t-held" in remaining
    assert len(result.erased_event_ids) == 1
    assert len(result.retained_event_ids) == 2


def test_retention_sweep_honors_hold_flag() -> None:
    log = InMemoryAuditLog()
    now = datetime(2026, 5, 13, tzinfo=timezone.utc)
    long_ago = now - timedelta(days=365 * 100)
    for held_kind in DEFAULT_AUDIT_RETENTION_POLICY.holds:
        record_audit_event(
            _make_event(kind=held_kind, timestamp=long_ago, trace_id=f"t-{held_kind}"),
            audit_log=log,
        )
    result = apply_audit_retention_policy(
        DEFAULT_AUDIT_RETENTION_POLICY,
        audit_log=log,
        clock=FixedClock(now),
    )
    assert result.erased_event_ids == ()
    assert len(result.retained_event_ids) == len(DEFAULT_AUDIT_RETENTION_POLICY.holds)


def test_retention_sweep_does_not_edit_in_place() -> None:
    log = InMemoryAuditLog()
    now = datetime(2026, 5, 13, tzinfo=timezone.utc)
    kept = _make_event(kind="tool_invoked", timestamp=now, trace_id="t-kept")
    record_audit_event(kept, audit_log=log)
    apply_audit_retention_policy(
        DEFAULT_AUDIT_RETENTION_POLICY,
        audit_log=log,
        clock=FixedClock(now),
    )
    after = list(log.iter_records())[0][1]
    assert after == kept


def test_retention_sweep_only_window_permits_deletion() -> None:
    log = InMemoryAuditLog()
    now = datetime(2026, 5, 13, tzinfo=timezone.utc)
    event_id = record_audit_event(
        _make_event(kind="tool_invoked", timestamp=now), audit_log=log
    )
    apply_audit_retention_policy(
        DEFAULT_AUDIT_RETENTION_POLICY,
        audit_log=log,
        clock=FixedClock(now),
    )
    with pytest.raises(AuditAppendOnlyViolation):
        log.delete(event_id)


def test_query_result_orders_by_timestamp_then_trace_id() -> None:
    log = InMemoryAuditLog()
    t0 = datetime(2026, 5, 13, 10, tzinfo=timezone.utc)
    t1 = datetime(2026, 5, 13, 11, tzinfo=timezone.utc)
    record_audit_event(_make_event(timestamp=t1, trace_id="b"), audit_log=log)
    record_audit_event(_make_event(timestamp=t0, trace_id="z"), audit_log=log)
    record_audit_event(_make_event(timestamp=t0, trace_id="a"), audit_log=log)

    result = query_audit_events(AuditQueryRequest(), audit_log=log)
    assert [r.trace_id for r in result.records] == ["a", "z", "b"]


def test_query_filter_by_kind_subset_is_closed_set() -> None:
    log = InMemoryAuditLog()
    record_audit_event(_make_event(kind="tool_invoked"), audit_log=log)
    record_audit_event(_make_event(kind="memory_read"), audit_log=log)
    record_audit_event(_make_event(kind="credential_access"), audit_log=log)
    result = query_audit_events(
        AuditQueryRequest(kind=frozenset({"tool_invoked", "credential_access"})),
        audit_log=log,
    )
    assert {r.kind for r in result.records} == {"tool_invoked", "credential_access"}


def test_query_filter_by_actor_ref_is_structural_exact() -> None:
    log = InMemoryAuditLog()
    record_audit_event(_make_event(actor_ref="alice"), audit_log=log)
    record_audit_event(_make_event(actor_ref="bob"), audit_log=log)
    result = query_audit_events(AuditQueryRequest(actor_ref="alice"), audit_log=log)
    assert [r.actor_ref for r in result.records] == ["alice"]


def test_query_filter_by_target_class_uses_prefix_only() -> None:
    log = InMemoryAuditLog()
    record_audit_event(_make_event(target_ref="memory:user/alice"), audit_log=log)
    record_audit_event(_make_event(target_ref="memory:user/bob"), audit_log=log)
    record_audit_event(_make_event(target_ref="tool:shell"), audit_log=log)
    result = query_audit_events(
        AuditQueryRequest(target_class="memory:user/"), audit_log=log
    )
    assert {r.target_ref for r in result.records} == {
        "memory:user/alice",
        "memory:user/bob",
    }


def test_query_filter_by_time_range_is_half_open() -> None:
    log = InMemoryAuditLog()
    t0 = datetime(2026, 5, 13, tzinfo=timezone.utc)
    record_audit_event(_make_event(timestamp=t0, trace_id="lo"), audit_log=log)
    record_audit_event(
        _make_event(timestamp=t0 + timedelta(hours=1), trace_id="mid"),
        audit_log=log,
    )
    record_audit_event(
        _make_event(timestamp=t0 + timedelta(hours=2), trace_id="hi"),
        audit_log=log,
    )
    result = query_audit_events(
        AuditQueryRequest(
            time_range=(t0, t0 + timedelta(hours=2))  # excludes "hi"
        ),
        audit_log=log,
    )
    assert {r.trace_id for r in result.records} == {"lo", "mid"}


def test_query_result_is_typed_cursor() -> None:
    result = query_audit_events(AuditQueryRequest(), audit_log=InMemoryAuditLog())
    assert isinstance(result, AuditQueryResult)
    assert result.records == ()


def test_soc2_scenario_template_kinds_are_frozen_subset() -> None:
    request = soc2_change_decision_query(actor_ref="op-1")
    assert request.kind == SOC2_CHANGE_DECISION_KINDS
    assert request.kind == frozenset(
        {"tool_invoked", "policy_decision", "intervention_issued"}
    )
    assert request.actor_ref == "op-1"


def test_gdpr_scenario_template_kinds_are_frozen_subset() -> None:
    request = gdpr_erasure_access_query(target_class="memory:user/alice")
    assert request.kind == GDPR_ERASURE_ACCESS_KINDS
    assert request.kind == frozenset(
        {"user_data_exported", "user_data_erased", "memory_read"}
    )
    assert request.target_class == "memory:user/alice"


def test_hipaa_scenario_template_kinds_are_frozen_subset() -> None:
    request = hipaa_sensitive_access_query(actor_ref="op-1")
    assert request.kind == HIPAA_SENSITIVE_ACCESS_KINDS
    assert request.kind == frozenset({"credential_access", "memory_read"})


def test_soc2_scenario_resolves_through_query_audit_events() -> None:
    log = InMemoryAuditLog()
    record_audit_event(
        _make_event(kind="tool_invoked", actor_ref="op-1"), audit_log=log
    )
    record_audit_event(_make_event(kind="memory_read", actor_ref="op-1"), audit_log=log)
    record_audit_event(
        _make_event(kind="policy_decision", actor_ref="op-1"), audit_log=log
    )
    record_audit_event(
        _make_event(kind="tool_invoked", actor_ref="op-2"), audit_log=log
    )
    result = query_audit_events(
        soc2_change_decision_query(actor_ref="op-1"), audit_log=log
    )
    assert {r.kind for r in result.records} == {"tool_invoked", "policy_decision"}
    assert all(r.actor_ref == "op-1" for r in result.records)


def test_audit_event_field_names_are_structural() -> None:
    fields = set(AuditEvent.__dataclass_fields__.keys())
    forbidden = {
        "llm_verdict",
        "model_judgement",
        "auto_detected",
        "prose_summary",
        "looks_sensitive",
        "sensitivity_score",
        "compliance_narrative",
    }
    assert fields.isdisjoint(forbidden)


def test_audit_retention_policy_field_names_are_structural() -> None:
    fields = set(AuditRetentionPolicy.__dataclass_fields__.keys())
    assert fields == {"durations", "holds", "erasure_eligible"}
    forbidden = {"llm_inferred", "model_recommended_retention"}
    assert fields.isdisjoint(forbidden)


def test_audit_query_request_field_names_are_structural() -> None:
    fields = set(AuditQueryRequest.__dataclass_fields__.keys())
    assert fields == {"kind", "actor_ref", "target_class", "time_range"}
    forbidden = {"prose_pattern", "natural_language_query", "model_filter"}
    assert fields.isdisjoint(forbidden)


def test_project_does_not_record() -> None:
    log = InMemoryAuditLog()
    project_runtime_event_to_audit_event(
        "canonical_tool_event",
        actor_ref="a",
        target_ref="t",
        timestamp=datetime(2026, 5, 13, tzinfo=timezone.utc),
        trace_id="trace-1",
        session_id="session-1",
        policy_ref="policy-1",
    )
    assert list(log.iter_records()) == []


def test_all_eight_kinds_route_through_one_project_record_pipeline() -> None:
    log = InMemoryAuditLog()
    t0 = datetime(2026, 5, 13, tzinfo=timezone.utc)
    recorded: list[AuditEventKind] = []
    for i, source in enumerate(AUDIT_RUNTIME_SOURCES):
        event = project_runtime_event_to_audit_event(
            source,
            actor_ref=f"actor-{i}",
            target_ref=f"target-{i}",
            timestamp=t0 + timedelta(seconds=i),
            trace_id=f"trace-{i}",
            session_id="session-1",
            policy_ref="policy-1",
        )
        record_audit_event(event, audit_log=log)
        recorded.append(event.kind)
    assert set(recorded) == set(AUDIT_EVENT_KINDS)
    assert len(list(log.iter_records())) == len(AUDIT_RUNTIME_SOURCES)
