from __future__ import annotations

from datetime import datetime, timedelta, timezone

from openminion.modules.memory.models import MemoryCandidate, MemoryRecord
from openminion.modules.memory.runtime.consolidation.extract import (
    extract_consolidation_payload,
)
from openminion.modules.memory.storage.memory import InMemoryMemoryStore


class _NoLLMMemoryAPI:
    def __init__(self, store: InMemoryMemoryStore) -> None:
        self._store = store
        self.llm_access_count = 0

    def candidate_list(self, options):  # noqa: ANN001
        return self._store.candidate_list(options)

    def list(self, options):  # noqa: ANN001
        return self._store.list(options)

    @property
    def client(self) -> object:
        self.llm_access_count += 1
        raise AssertionError("Phase 1 extraction must not access any LLM client")


def test_extract_consolidation_payload_respects_default_recent_rollout_limit() -> None:
    store = InMemoryMemoryStore()
    for idx in range(300):
        store.candidate_put(
            MemoryCandidate(
                candidate_id=f"cand-{idx:03d}",
                session_id="session-1",
                proposed_scope="agent:test-agent",
                type="fact",
                title=f"Candidate {idx}",
                content=f"content {idx}",
                confidence=0.4,
            )
        )

    payload = extract_consolidation_payload(
        store,
        session_id="session-1",
        agent_id="test-agent",
    )

    assert payload.evidence_window["recent_rollout_limit"] == 256
    assert len(payload.candidate_refs) == 256
    assert payload.candidate_refs[0]["candidate_id"] == "cand-000"
    assert payload.candidate_refs[-1]["candidate_id"] == "cand-255"


def test_extract_consolidation_payload_is_deterministic_and_bti_aware() -> None:
    store = InMemoryMemoryStore()
    now = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
    record = MemoryRecord(
        id="mem-1",
        scope="agent:test-agent",
        type="fact",
        key="fact:deploy-region",
        title="Deploy region",
        content="Current deploy region is us-west-2.",
        source="validated",
        confidence=0.9,
        meta={"claim_key": "deploy-region", "polarity": "negates"},
        created_at=(now - timedelta(days=7)).isoformat(),
        updated_at=(now - timedelta(days=7)).isoformat(),
        event_time=(now - timedelta(days=7)).isoformat(),
        valid_to=(now - timedelta(days=1)).isoformat(),
    )
    store.put(record)
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-1",
            session_id="session-1",
            proposed_scope="agent:test-agent",
            type="fact",
            key="fact:deploy-region",
            title="Deploy region",
            content="Deploy token sk-1234567890abcdef1234567890abcdef is us-west-2.",
            confidence=0.8,
            meta={"normalized_key": "fact:deploy-region"},
            claim_key="deploy-region",
            polarity="asserts",
        )
    )

    payload_one = extract_consolidation_payload(
        store,
        session_id="session-1",
        agent_id="test-agent",
        recent_rollout_limit=64,
        now=now,
    )
    payload_two = extract_consolidation_payload(
        store,
        session_id="session-1",
        agent_id="test-agent",
        recent_rollout_limit=64,
        now=now,
    )

    assert payload_one == payload_two
    assert payload_one.candidate_refs[0]["content_preview"].find("sk-") == -1
    assert "[REDACTED_SECRET]" in payload_one.candidate_refs[0]["content_preview"]
    assert payload_one.duplicate_hints == [
        {
            "candidate_id": "cand-1",
            "normalized_key": "fact:deploy-region",
            "existing_record_id": "mem-1",
            "existing_event_time": record.event_time,
            "existing_valid_to": record.valid_to,
        }
    ]
    assert payload_one.contradiction_hints == [
        {
            "candidate_id": "cand-1",
            "record_id": "mem-1",
            "claim_key": "deploy-region",
            "candidate_polarity": "asserts",
            "record_polarity": "negates",
            "record_event_time": record.event_time,
            "record_valid_to": record.valid_to,
            "record_is_current": False,
        }
    ]


def test_extract_consolidation_payload_never_touches_llm_clients() -> None:
    store = InMemoryMemoryStore()
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-1",
            session_id="session-1",
            proposed_scope="agent:test-agent",
            type="fact",
            title="Fact",
            content="something useful",
            confidence=0.5,
        )
    )
    api = _NoLLMMemoryAPI(store)

    payload = extract_consolidation_payload(
        api,
        session_id="session-1",
        agent_id="test-agent",
    )

    assert len(payload.candidate_refs) == 1
    assert api.llm_access_count == 0
