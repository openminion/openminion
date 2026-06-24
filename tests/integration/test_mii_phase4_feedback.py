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


def _memory_config():
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


def test_phase4_multi_turn_auto_extract_promotion_and_capsule(tmp_path: Path) -> None:
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    service = MemoryService(store=store)
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="phase4-agent",
        memory_config=_memory_config(),
    )

    # (prose→candidate auto-extraction moved to brain AFE; see BAFE
    service.candidate_put(
        MemoryCandidate(
            candidate_id="cand-phase4-dark",
            session_id="s-learn",
            proposed_scope="agent:phase4-agent",
            type="user_preference",
            title="dark mode preference",
            content="I prefer dark mode.",
            confidence=0.7,
            meta={"reconfirmation_count": 3, "retrieval_hit_count": 3},
        )
    )
    assert (
        len(
            service.candidate_list(
                CandidateListOptions(session_id="s-learn", status="proposed", limit=10)
            )
        )
        == 1
    )

    adapter.record_turn(
        session_id="s-learn",
        run_id="r2",
        request_id="req2",
        channel="console",
        target="chat",
        user_message="What theme should I use?",
        assistant_message="You prefer dark mode.",
    )

    adapter.record_turn(
        session_id="s-learn",
        run_id="r3",
        request_id="req3",
        channel="console",
        target="chat",
        user_message="Thanks.",
        assistant_message="Happy to help.",
    )

    facts = service.list(
        ListQueryOptions(
            scopes=["agent:phase4-agent"],
            types=["user_preference"],
            limit=20,
        )
    )
    assert any("dark mode" in str(record.content).lower() for record in facts)

    capsule, _ = adapter.build_context_with_metadata(
        session_id="fresh-phase4",
        user_message="dark mode",
    )
    assert "dark mode" in capsule.lower()
