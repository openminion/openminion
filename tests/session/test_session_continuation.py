from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from pydantic import ValidationError

from openminion.modules.context.schemas import (
    BuildPackRequest,
    ContextBudgets,
    SessionSlice,
    MemoryCard,
)
from openminion.modules.context.segment import (
    _filter_continuation_duplicate_memory_cards,
)
from openminion.modules.context.segment.render import (
    _SegmentAssemblyRuntime,
    append_summary_segments,
)
from openminion.modules.session.interfaces import SESSION_CONTINUATION_SCHEMA_VERSION
from openminion.modules.session.runtime.continuation import (
    PACKET_APPLIED,
    PACKET_CREATED,
    PACKET_EXPIRED,
    PACKET_REJECTED,
    SessionContinuationService,
)
from openminion.modules.session.schemas import SessionContinuationPayload
from openminion.modules.session.schemas import ContinuationError
from openminion.modules.session.storage.sqlite_store import SQLiteSessionStore


def _store(tmp_path) -> SQLiteSessionStore:
    return SQLiteSessionStore(tmp_path / "sessions.db")


def _seed_source(store: SQLiteSessionStore, *, session_id: str = "source") -> str:
    store.create_session(session_id=session_id, initial_agent_id="agent-a")
    store.put_working_state(
        session_id,
        state_inline={
            "phase": "act",
            "cursor": 2,
            "session_work_summary": "Migration is staged; verify the final index.",
            "intent_execution_states": [
                {"intent_id": "verify-index", "status": "pending"}
            ],
            "unresolved_clarify_items": [{"clarification_id": "confirm-window"}],
            "decision_memory_refs": ["memory-1"],
            "permission_refs": ["permission-1"],
        },
    )
    store.append_event(
        session_id,
        event_type="tool.call.completed",
        payload={"tool_name": "file.read", "status": "completed"},
        refs={"artifact_refs": ["artifact-1"]},
    )
    return session_id


def _target(
    store: SQLiteSessionStore,
    *,
    session_id: str = "target",
    agent_id: str = "agent-a",
) -> str:
    store.create_session(session_id=session_id, initial_agent_id=agent_id)
    return session_id


def test_payload_rejects_unknown_version_expiry_and_secret() -> None:
    base: dict[str, Any] = {
        "created_at_ms": 1_000,
        "expires_at_ms": 2_000,
        "source_session_id": "source",
        "source_latest_seq": 1,
        "source_agent_id": "agent-a",
        "target_agent_id": "agent-a",
    }
    payload = SessionContinuationPayload.model_validate(base)
    assert payload.schema_version == SESSION_CONTINUATION_SCHEMA_VERSION

    with pytest.raises(ValidationError, match="unsupported_continuation_schema"):
        SessionContinuationPayload.model_validate(
            {**base, "schema_version": "session_continuation.v2"}
        )
    with pytest.raises(ValidationError, match="invalid_continuation_expiry"):
        SessionContinuationPayload.model_validate(
            {**base, "expires_at_ms": 700_000_000}
        )
    with pytest.raises(ValidationError, match="continuation_forbidden_field"):
        SessionContinuationPayload.model_validate(
            {**base, "session_work_summary": "Bearer private-value"}
        )


def test_preview_is_side_effect_free_and_create_reconstructs_event(tmp_path) -> None:
    store = _store(tmp_path)
    source_id = _seed_source(store)
    service = SessionContinuationService(store, now_ms=lambda: 10_000)
    before = store.latest_event_seq(source_id)

    preview = service.preview(source_id, target_agent_id="agent-a")

    assert store.latest_event_seq(source_id) == before
    assert preview.payload.source_latest_seq == before
    assert preview.payload.session_work_summary.startswith("Migration is staged")
    assert preview.payload.permission_refs == ["permission-1"]
    assert "permission_revalidation_required" in preview.warnings

    result = service.create(source_id, target_agent_id="agent-a")
    assert result.packet is not None
    assert result.packet.payload == preview.payload
    assert result.packet.event_seq > result.packet.payload.source_latest_seq
    assert (
        store.get_event_by_id(result.packet.packet_id)["event_type"] == PACKET_CREATED
    )


def test_apply_is_idempotent_and_conflicting_target_is_rejected(tmp_path) -> None:
    store = _store(tmp_path)
    source_id = _seed_source(store)
    first_target = _target(store)
    second_target = _target(store, session_id="target-2")
    service = SessionContinuationService(store, now_ms=lambda: 10_000)
    packet = service.create(source_id, target_agent_id="agent-a").packet
    assert packet is not None

    first = service.apply(first_target, packet_id=packet.packet_id)
    repeated = service.apply(first_target, packet_id=packet.packet_id)
    conflict = service.apply(second_target, packet_id=packet.packet_id)

    assert first.status == "applied"
    assert repeated.status == "already_applied"
    assert repeated.target_event_id == first.target_event_id
    assert conflict.reason_code == "continuation_target_conflict"
    applied = store.get_events_by_parent_and_type(packet.packet_id, PACKET_APPLIED)
    assert len(applied) == 1
    assert applied[0]["session_id"] == first_target
    assert applied[0]["parent_event_id"] == packet.packet_id
    assert store.get_latest_working_state(first_target) is None

    with pytest.raises(sqlite3.IntegrityError):
        store.append_event(
            second_target,
            event_type=PACKET_APPLIED,
            parent_event_id=packet.packet_id,
            payload={"packet_id": packet.packet_id},
        )
    assert (
        len(store.get_events_by_parent_and_type(packet.packet_id, PACKET_APPLIED)) == 1
    )


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("agent", "continuation_agent_mismatch"),
        ("nonempty", "continuation_target_not_empty"),
        ("expired", "continuation_expired"),
    ],
)
def test_apply_failures_write_no_target_projection(
    tmp_path,
    case: str,
    expected: str,
) -> None:
    store = _store(tmp_path)
    source_id = _seed_source(store)
    target_id = _target(
        store,
        agent_id="agent-b" if case == "agent" else "agent-a",
    )
    now = [10_000]
    service = SessionContinuationService(store, now_ms=lambda: now[0])
    packet = service.create(
        source_id,
        target_agent_id="agent-a",
        expires_in_seconds=1,
    ).packet
    assert packet is not None
    if case == "nonempty":
        store.append_turn(target_id, "user", "already started")
    if case == "expired":
        now[0] = 11_000

    result = service.apply(target_id, packet_id=packet.packet_id)

    assert result.status == "rejected"
    assert result.reason_code == expected
    assert store.get_events(target_id, types=[PACKET_APPLIED]) == []
    audit_type = PACKET_EXPIRED if case == "expired" else PACKET_REJECTED
    assert (
        store.get_events(source_id, types=[audit_type])[-1]["payload"]["reason_code"]
        == expected
    )


def test_context_projection_is_first_pinned_and_within_summary_budget(tmp_path) -> None:
    store = _store(tmp_path)
    source_id = _seed_source(store)
    target_id = _target(store)
    service = SessionContinuationService(store, now_ms=lambda: 10_000)
    packet = service.create(source_id, target_agent_id="agent-a").packet
    assert packet is not None
    service.apply(target_id, packet_id=packet.packet_id)
    raw_slice = store.get_slice(target_id, "chat", None)
    continuation = raw_slice["continuation"]
    session_slice = SessionSlice(
        session_id=target_id,
        slice_version="test",
        summary_short="regular summary",
        active_state={
            "session_work_summary": packet.payload.session_work_summary,
        },
        continuation=continuation,
    )
    budgets = ContextBudgets(
        total_max_tokens=1_000,
        identity_tokens=40,
        summary_tokens=80,
        recent_turn_tokens=40,
        facts_tokens=0,
        memory_tokens=0,
        skills_tokens=0,
        artifact_tokens=0,
        instructions_tokens=40,
    )
    runtime = _SegmentAssemblyRuntime(
        budgets=budgets,
        fit_to_budget=lambda text, cap: (text[: cap * 4], len(text) > cap * 4),
        estimate_tokens=lambda text: max(1, (len(text) + 3) // 4),
    )
    append_summary_segments(
        runtime,
        request=BuildPackRequest(
            session_id=target_id,
            agent_id="agent-a",
            purpose="chat",
            query="continue",
        ),
        session_slice=session_slice,
        seed_text=None,
        rolling_enabled=True,
        compression_enabled=False,
        compressctl=None,
    )

    summary_segments = [item for item in runtime.segments if item.bucket == "summaries"]
    assert summary_segments[0].id == "continuation"
    assert summary_segments[0].pinned is True
    assert (
        sum(item.token_estimate for item in summary_segments) <= budgets.summary_tokens
    )
    assert [item.id for item in summary_segments].count("session_work_summary") == 0


def test_migration_creates_parent_type_index(tmp_path) -> None:
    store = _store(tmp_path)
    rows = store._record_store.query_dicts(  # noqa: SLF001
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        ("idx_session_events_parent_type",),
    )
    assert rows == [{"name": "idx_session_events_parent_type"}]
    unique_rows = store._record_store.query_dicts(  # noqa: SLF001
        "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
        ("idx_session_continuation_single_apply",),
    )
    assert unique_rows == [{"name": "idx_session_continuation_single_apply"}]


def test_telemetry_is_bounded_and_contains_no_packet_content(tmp_path) -> None:
    store = _store(tmp_path)
    source_id = _seed_source(store)
    target_id = _target(store)
    emitted: list[tuple[str, dict[str, Any]]] = []
    service = SessionContinuationService(
        store,
        now_ms=lambda: 10_000,
        telemetry_sink=lambda event_type, data: emitted.append((event_type, data)),
    )

    packet = service.create(source_id, target_agent_id="agent-a").packet
    assert packet is not None
    service.apply(target_id, packet_id=packet.packet_id)

    assert [event_type for event_type, _ in emitted] == [
        "session.continuation.build",
        "session.continuation.apply",
    ]
    serialized = repr(emitted)
    assert "Migration is staged" not in serialized
    assert "permission-1" not in serialized
    assert "packet" not in serialized.lower()


def test_duplicate_memory_refs_are_suppressed_without_hiding_unrelated_memory() -> None:
    cards = [
        MemoryCard(record_id="memory-1", record_type="plan_snapshot", text="duplicate"),
        MemoryCard(record_id="memory-2", record_type="fact", text="unrelated"),
    ]
    continuation = {
        "continuation": {
            "memory_refs": ["memory-1"],
            "session_work_summary_ref": None,
        }
    }

    filtered = _filter_continuation_duplicate_memory_cards(cards, continuation)

    assert [card.record_id for card in filtered] == ["memory-2"]


def test_missing_cross_store_packet_and_partial_write_fail_closed(
    tmp_path,
    monkeypatch,
) -> None:
    store = _store(tmp_path)
    source_id = _seed_source(store)
    target_id = _target(store)
    service = SessionContinuationService(store, now_ms=lambda: 10_000)
    with pytest.raises(ContinuationError, match="continuation_packet_not_found"):
        service.apply(target_id, packet_id="missing")

    packet = service.create(source_id, target_agent_id="agent-a").packet
    assert packet is not None
    original_append = store.append_event

    def fail_apply(session_id: str, *args: Any, **kwargs: Any) -> str:
        if kwargs.get("event_type") == PACKET_APPLIED:
            raise RuntimeError("simulated write failure")
        return original_append(session_id, *args, **kwargs)

    monkeypatch.setattr(store, "append_event", fail_apply)
    with pytest.raises(RuntimeError, match="simulated write failure"):
        service.apply(target_id, packet_id=packet.packet_id)
    assert store.get_events(target_id, types=[PACKET_APPLIED]) == []
