from __future__ import annotations

from datetime import datetime, timezone

from openminion.modules.runtime.audit import (
    AuditEvent,
    InMemoryAuditLog,
    project_runtime_event_to_audit_event,
    record_audit_event,
)


def _emit(
    log: InMemoryAuditLog,
    source: str,
    *,
    actor_ref: str,
    target_ref: str,
    trace_id: str,
    policy_ref: str,
    redaction_mode: str = "none",
    artifact_refs: tuple[str, ...] = (),
) -> AuditEvent:
    event = project_runtime_event_to_audit_event(
        source,  # type: ignore[arg-type]
        actor_ref=actor_ref,
        target_ref=target_ref,
        timestamp=datetime.now(timezone.utc),
        trace_id=trace_id,
        session_id="session-1",
        policy_ref=policy_ref,
        artifact_refs=artifact_refs,
        redaction_mode=redaction_mode,
    )
    record_audit_event(event, audit_log=log)
    return event


def test_memory_export_call_emits_exactly_one_user_data_exported() -> None:
    log = InMemoryAuditLog()
    _emit(
        log,
        "memory_export_event",
        actor_ref="operator-1",
        target_ref="memory:bundle/alice",
        trace_id="trace-export-1",
        policy_ref="gdpr.export.v1",
        artifact_refs=("bundle:alice.zip",),
    )
    records = list(log.iter_records())
    assert len(records) == 1
    assert records[0][1].kind == "user_data_exported"


def test_memory_erase_pass_emits_one_user_data_erased_per_record() -> None:
    log = InMemoryAuditLog()
    erased_record_ids = ("rec-1", "rec-2", "rec-3")
    for rec_id in erased_record_ids:
        _emit(
            log,
            "memory_erase_event",
            actor_ref="gc",
            target_ref=f"memory:record/{rec_id}",
            trace_id=f"trace-erase-{rec_id}",
            policy_ref="memory.retention.v1",
        )
    kinds = [event.kind for _id, event in log.iter_records()]
    assert kinds == ["user_data_erased"] * len(erased_record_ids)
    assert len(kinds) == len(erased_record_ids)


def test_turn_cancel_surface_emits_intervention_issued() -> None:
    log = InMemoryAuditLog()
    _emit(
        log,
        "intervention_recorded_event",
        actor_ref="operator-1",
        target_ref="trace-123",
        trace_id="trace-123",
        policy_ref="ops.cancel.v1",
    )
    records = list(log.iter_records())
    assert len(records) == 1
    assert records[0][1].kind == "intervention_issued"


def test_runtime_kill_surface_emits_intervention_issued() -> None:
    log = InMemoryAuditLog()
    _emit(
        log,
        "intervention_recorded_event",
        actor_ref="operator-1",
        target_ref="runtime:manager",
        trace_id="kill-1",
        policy_ref="ops.kill.v1",
    )
    records = list(log.iter_records())
    assert len(records) == 1
    assert records[0][1].kind == "intervention_issued"


def test_four_surface_ingestion_audit_event_count_parity() -> None:
    log = InMemoryAuditLog()
    for i in range(2):
        _emit(
            log,
            "memory_export_event",
            actor_ref="op",
            target_ref=f"memory:bundle/{i}",
            trace_id=f"export-{i}",
            policy_ref="gdpr.v1",
        )
    for i in range(3):
        _emit(
            log,
            "memory_erase_event",
            actor_ref="gc",
            target_ref=f"memory:record/{i}",
            trace_id=f"erase-{i}",
            policy_ref="memory.retention.v1",
        )
    _emit(
        log,
        "intervention_recorded_event",
        actor_ref="op",
        target_ref="trace-x",
        trace_id="trace-x",
        policy_ref="ops.cancel.v1",
    )
    _emit(
        log,
        "intervention_recorded_event",
        actor_ref="op",
        target_ref="runtime:manager",
        trace_id="kill-y",
        policy_ref="ops.kill.v1",
    )

    records = list(log.iter_records())
    assert len(records) == 2 + 3 + 1 + 1
    kind_counts: dict[str, int] = {}
    for _id, event in records:
        kind_counts[event.kind] = kind_counts.get(event.kind, 0) + 1
    assert kind_counts == {
        "user_data_exported": 2,
        "user_data_erased": 3,
        "intervention_issued": 2,
    }
