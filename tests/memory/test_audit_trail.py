from __future__ import annotations

from pathlib import Path
from dataclasses import replace

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.models import MemoryCandidate, MemoryRecord
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage import (
    AuditedMemoryStore,
    InMemoryMemoryAuditSink,
    SQLiteMemoryAuditSink,
)
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter
from openminion.services.runtime.bootstrap import build_agent_memory_service
from tests._csc_fixtures import _csc_install_default_agent


def _record(record_id: str) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope="session:s1",
        type="fact",
        title=f"title-{record_id}",
        content={"text": f"content-{record_id}"},
        created_at="2026-04-30T00:00:00+00:00",
        updated_at="2026-04-30T00:00:00+00:00",
    )


def _candidate(candidate_id: str) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        session_id="s1",
        proposed_scope="session:s1",
        type="fact",
        title=f"title-{candidate_id}",
        content={"text": f"candidate-{candidate_id}"},
        status="approved",
    )


def _build_config() -> OpenMinionConfig:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.memory_enabled = True
    config.runtime.memory_provider = "memory_v2"
    return config


def _memory_module_config():
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return replace(
        cfg,
        candidate_learning=replace(
            cfg.candidate_learning,
            auto_extract_enabled=True,
            auto_extract_notify=True,
        ),
    )


def test_audited_memory_store_records_put_delete_and_promote_events() -> None:
    sink = InMemoryMemoryAuditSink()
    store = AuditedMemoryStore(InMemoryMemoryStore(), sink=sink)

    store.put(_record("r1"))
    store.delete("r1")
    store.candidate_put(_candidate("c1"))
    promoted = store.promote_candidate("c1", "agent:main")

    event_types = [event.event_type for event in sink.events]
    assert event_types == [
        "memory.record.put",
        "memory.record.delete",
        "memory.candidate.put",
        "memory.candidate.promote",
    ]
    assert promoted.scope == "agent:main"


def test_audited_memory_store_catches_direct_store_bypass() -> None:
    sink = InMemoryMemoryAuditSink()
    service = MemoryService(store=AuditedMemoryStore(InMemoryMemoryStore(), sink=sink))

    record_id = service.write_record(
        scope="session:s1",
        record_type="fact",
        title="alpha",
        content={"text": "alpha"},
    )
    service._store.delete(record_id)  # noqa: SLF001

    event_types = [event.event_type for event in sink.events]
    assert "memory.record.put" in event_types
    assert "memory.record.delete" in event_types


def test_audited_memory_store_records_invalidate_event() -> None:
    sink = InMemoryMemoryAuditSink()
    service = MemoryService(store=AuditedMemoryStore(InMemoryMemoryStore(), sink=sink))

    record_id = service.write_record(
        scope="session:s1",
        record_type="fact",
        title="alpha",
        content={"text": "alpha"},
    )
    service.invalidate(
        record_id,
        valid_to="2026-05-21T00:00:00+00:00",
        reason="superseded by newer fact",
    )

    invalidate_events = [
        event for event in sink.events if event.event_type == "memory.record.invalidate"
    ]
    assert len(invalidate_events) == 1
    assert invalidate_events[0].target_id == record_id
    assert invalidate_events[0].details["reason"] == "superseded by newer fact"


def test_audited_memory_store_tolerates_missing_sink() -> None:
    store = AuditedMemoryStore(InMemoryMemoryStore(), sink=None)
    record_id = store.put(_record("r1"))
    store.delete(record_id)
    assert store.get(record_id) is not None


def test_build_agent_memory_service_wires_sqlite_audit_sink(tmp_path) -> None:
    adapter = build_agent_memory_service(
        config=_build_config(),
        agent_id="mat-agent",
        memory_root=tmp_path,
        logger=__import__("logging").getLogger("mat.bootstrap"),
        retrieve_ctl=None,
    )
    assert isinstance(adapter, MemoryServiceGatewayAdapter)

    service = getattr(adapter, "_service")

    record_id = service.write_record(
        scope="session:s1",
        record_type="fact",
        title="bootstrap-audit",
        content={"text": "bootstrap-audit"},
    )
    assert record_id

    audit_db = tmp_path / "memory.audit.db"
    events = SQLiteMemoryAuditSink(audit_db).list_events()
    assert any(item["event_type"] == "memory.record.put" for item in events)


def test_sqlite_audit_sink_records_upsert_feedback_and_promote(tmp_path) -> None:
    sink = SQLiteMemoryAuditSink(tmp_path / "memory.audit.db")
    store = AuditedMemoryStore(
        SQLiteMemoryStore(Path(tmp_path) / "memory.db"), sink=sink
    )

    store.upsert("session:s1", "fact", "theme", {"content": {"color": "red"}})
    store.apply_outcome_feedback(
        ["missing", "missing"],
        outcome="success",
        command_id="cmd-1",
        observed_at="2026-04-30T00:00:00+00:00",
        feedback_delta=0.1,
    )
    store.candidate_put(_candidate("c2"))
    store.promote_candidate("c2", "agent:main")

    event_types = [item["event_type"] for item in sink.list_events()]
    assert "memory.record.upsert" in event_types
    assert "memory.candidate.promote" in event_types


def test_trust_gate_events_are_visible_via_existing_sqlite_audit_query_path(
    tmp_path,
) -> None:
    sink = SQLiteMemoryAuditSink(tmp_path / "memory.audit.db")
    store = AuditedMemoryStore(
        SQLiteMemoryStore(Path(tmp_path) / "memory.db"),
        sink=sink,
    )
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="audit-agent",
        memory_config=_memory_module_config(),
    )
    store.put(
        MemoryRecord(
            id="mem-corroborate",
            scope="agent:audit-agent",
            type="user_preference",
            key="pref:existing",
            title="Preference: dark mode",
            content="I prefer dark mode.",
            source="validated",
            confidence=0.9,
            meta={"claim_key": "pref:dark_mode", "polarity": "asserts"},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )
    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-trust-audit",
            session_id="session-a",
            proposed_scope="agent:audit-agent",
            type="user_preference",
            title="Preference: dark mode",
            content="I prefer dark mode.",
            confidence=0.9,
            claim_key="pref:dark_mode",
            source_class="llm_extracted",
            meta={"reconfirmation_count": 2, "retrieval_hit_count": 3},
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )

    adapter._promote_mature_candidates(  # noqa: SLF001
        "session-a",
        user_message="",
        assistant_message="",
    )

    trust_events = [
        item
        for item in sink.list_events()
        if item["event_type"] == "memory.trust_gate.evaluate"
    ]

    assert len(trust_events) == 1
    assert trust_events[0]["details"]["decision"] == "ALLOWED"
    assert trust_events[0]["details"]["reason_code"] == "ALLOWED"
    assert trust_events[0]["details"]["trust_score"] == 0.75


def test_trust_gate_emits_for_each_blocked_promotion_attempt() -> None:
    sink = InMemoryMemoryAuditSink()
    service = MemoryService(store=AuditedMemoryStore(InMemoryMemoryStore(), sink=sink))
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="audit-agent",
        memory_config=_memory_module_config(),
    )
    for idx in range(5):
        service.candidate_put(
            MemoryCandidate(
                candidate_id=f"cand-blocked-{idx}",
                session_id="session-a",
                proposed_scope="agent:audit-agent",
                type="user_preference",
                title="Preference: dark mode",
                content="I prefer dark mode.",
                confidence=0.9,
                claim_key=f"pref:dark_mode:{idx}",
                source_class="agent_inferred",
                meta={"reconfirmation_count": 2, "retrieval_hit_count": 3},
                created_at="2026-01-01T00:00:00+00:00",
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )

    promoted = adapter._promote_mature_candidates(  # noqa: SLF001
        "session-a",
        user_message="",
        assistant_message="",
    )
    trust_events = [
        event
        for event in sink.events
        if event.event_type == "memory.trust_gate.evaluate"
    ]

    assert promoted == 0
    assert len(trust_events) == 5
    assert all(event.details["decision"] == "BLOCKED" for event in trust_events)
    assert all(
        event.details["reason_code"] == "BELOW_TRUST_THRESHOLD"
        for event in trust_events
    )
