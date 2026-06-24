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
            promotion_readiness_threshold=0.6,
        ),
    )


def test_success_provenance_candidate_promotes_ahead_of_neutral_peer(
    tmp_path: Path,
) -> None:
    service = MemoryService(store=SQLiteMemoryStore(tmp_path / "memory.db"))
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="phase10-agent",
        memory_config=_memory_config(),
    )
    success_candidate = MemoryCandidate(
        candidate_id="cand-success",
        session_id="session-a",
        proposed_scope="agent:phase10-agent",
        type="procedure",
        title="Procedure: deploy with rollback rehearsal",
        content="Run a rollback rehearsal before rollout.",
        confidence=0.5,
        meta={
            "reconfirmation_count": 1,
            "retrieval_hit_count": 1,
            "source_success_path": True,
            "source_outcome_status": "success",
        },
        created_at="2026-01-20T00:00:00+00:00",
        updated_at="2026-01-20T00:00:00+00:00",
    )
    neutral_candidate = MemoryCandidate(
        candidate_id="cand-neutral",
        session_id="session-a",
        proposed_scope="agent:phase10-agent",
        type="procedure",
        title="Procedure: deploy with rollback rehearsal",
        content="Run a rollback rehearsal before rollout.",
        confidence=0.5,
        meta={
            "reconfirmation_count": 1,
            "retrieval_hit_count": 1,
        },
        created_at="2026-01-20T00:00:00+00:00",
        updated_at="2026-01-20T00:00:00+00:00",
    )
    service.candidate_put(success_candidate)
    service.candidate_put(neutral_candidate)

    promoted = adapter._promote_mature_candidates(  # noqa: SLF001
        "session-a",
        user_message="",
        assistant_message="",
    )

    remaining_candidates = service.candidate_list(
        CandidateListOptions(
            proposed_scope="agent:phase10-agent",
            status="proposed",
            limit=10,
        )
    )
    promoted_records = service.list(
        ListQueryOptions(
            scopes=["agent:phase10-agent"],
            types=["procedure"],
            limit=10,
        )
    )

    assert promoted == 1
    assert [record.title for record in promoted_records] == [
        "Procedure: deploy with rollback rehearsal"
    ]
    assert {candidate.candidate_id for candidate in remaining_candidates} == {
        "cand-neutral"
    }


def test_negative_outcome_provenance_does_not_gain_readiness_advantage(
    tmp_path: Path,
) -> None:
    service = MemoryService(store=SQLiteMemoryStore(tmp_path / "memory.db"))
    adapter = MemoryServiceGatewayAdapter(
        service,
        agent_id="phase10-agent",
        memory_config=_memory_config(),
    )
    failed_candidate = MemoryCandidate(
        candidate_id="cand-failed",
        session_id="session-a",
        proposed_scope="agent:phase10-agent",
        type="procedure",
        title="Procedure: failed deploy path",
        content="Skip rollback rehearsal before rollout.",
        confidence=0.5,
        meta={
            "reconfirmation_count": 1,
            "retrieval_hit_count": 1,
            "source_outcome_status": "failed",
        },
        created_at="2026-01-20T00:00:00+00:00",
        updated_at="2026-01-20T00:00:00+00:00",
    )
    neutral_candidate = MemoryCandidate(
        candidate_id="cand-neutral",
        session_id="session-a",
        proposed_scope="agent:phase10-agent",
        type="procedure",
        title="Procedure: neutral deploy path",
        content="Run smoke tests before rollout.",
        confidence=0.5,
        meta={
            "reconfirmation_count": 1,
            "retrieval_hit_count": 1,
        },
        created_at="2026-01-20T00:00:00+00:00",
        updated_at="2026-01-20T00:00:00+00:00",
    )
    service.candidate_put(failed_candidate)
    service.candidate_put(neutral_candidate)

    promoted = adapter._promote_mature_candidates(  # noqa: SLF001
        "session-a",
        user_message="",
        assistant_message="",
    )

    remaining_candidates = service.candidate_list(
        CandidateListOptions(
            proposed_scope="agent:phase10-agent",
            status="proposed",
            limit=10,
        )
    )
    assert promoted == 0
    assert {candidate.candidate_id for candidate in remaining_candidates} == {
        "cand-failed",
        "cand-neutral",
    }
