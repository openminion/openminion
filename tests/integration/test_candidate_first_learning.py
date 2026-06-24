from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.memory.config import from_base_config
from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.base import (
    CandidateListOptions,
    ListQueryOptions,
)
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _memory_config(*, auto_extract_enabled: bool = True):
    cfg = from_base_config(
        base_config=OpenMinionConfig(),
        home_root=Path("/tmp/openminion-home"),
        data_root=Path("/tmp/openminion-data"),
    )
    return replace(
        cfg,
        candidate_learning=replace(
            cfg.candidate_learning,
            auto_extract_enabled=auto_extract_enabled,
            auto_extract_notify=True,
        ),
    )


def test_candidate_first_learning_promotes_across_sessions(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="candidate-agent",
        memory_config=_memory_config(),
    )

    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-dark-mode",
            session_id="session-1",
            proposed_scope="agent:candidate-agent",
            type="user_preference",
            title="dark mode preference",
            content="I prefer dark mode.",
            confidence=0.7,
            claim_key="pref:dark_mode",
            source_class="user_input",
            meta={"reconfirmation_count": 3, "retrieval_hit_count": 3},
        )
    )

    staged = service.candidate_list(
        CandidateListOptions(proposed_scope="agent:candidate-agent", status="proposed")
    )
    assert len(staged) == 1
    assert staged[0].session_id == "session-1"

    adapter.record_turn(
        session_id="session-2",
        run_id="run-2",
        request_id="req-2",
        channel="console",
        target="chat",
        user_message="I still prefer dark mode.",
        assistant_message="You still prefer dark mode.",
    )

    adapter.record_turn(
        session_id="session-3",
        run_id="run-3",
        request_id="req-3",
        channel="console",
        target="chat",
        user_message="Thanks.",
        assistant_message="Happy to help.",
    )

    promoted = service.list(
        ListQueryOptions(
            scopes=["agent:candidate-agent"],
            types=["user_preference"],
            limit=10,
        )
    )
    remaining = service.candidate_list(
        CandidateListOptions(proposed_scope="agent:candidate-agent", status="proposed")
    )

    assert any("dark mode" in str(record.content).lower() for record in promoted)
    assert remaining == []


def test_candidate_first_learning_gc_rejects_stale_candidates(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="candidate-agent",
        memory_config=_memory_config(),
    )

    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-old",
            session_id="session-old",
            proposed_scope="agent:candidate-agent",
            type="fact",
            title="Shell note",
            content="My shell is fish.",
            confidence=0.4,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
    )

    adapter.record_turn(
        session_id="session-new",
        run_id="run-gc",
        request_id="req-gc",
        channel="console",
        target="chat",
        user_message="Nothing new today.",
        assistant_message="Okay.",
    )

    candidate = service.candidate_get("cand-old")
    assert candidate is not None
    assert candidate.status == "rejected"
    assert candidate.review is not None
    assert candidate.review.note == "gc_expired"
