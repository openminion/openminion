from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.memory.config import ConsolidationConfig
from openminion.modules.memory.models import MemoryCandidate
from openminion.modules.memory.runtime.consolidation import (
    ConsolidationCoordinator,
    ExtractionPayload,
    MergeDecision,
    MergeDecisions,
    apply_memory_consolidation_decisions,
    collect_memory_consolidation_candidates,
)
from openminion.modules.memory.runtime.consolidation.extract import (
    collect_memory_consolidation_candidates as collect_from_extract,
)
from openminion.modules.memory.runtime.consolidation.merge import (
    apply_memory_consolidation_decisions as apply_from_merge,
)
from openminion.modules.llm.providers.factory import RuntimeLLMHandle
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.memory.storage.base import ListQueryOptions
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.memory.service import MemoryService


def test_collect_memory_consolidation_candidates_returns_bounded_batch() -> None:
    store = InMemoryMemoryStore()
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-1",
            session_id="s1",
            proposed_scope="agent:test-agent",
            type="fact",
            title="Deploy region",
            content="Preferred deploy region is us-west-2.",
            confidence=0.7,
        )
    )
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-2",
            session_id="s2",
            proposed_scope="agent:test-agent",
            type="tool_outcome",
            title="Build failure",
            content="File write failed because the workspace was read-only.",
            confidence=0.5,
        )
    )

    batch = collect_memory_consolidation_candidates(
        store,
        proposed_scope="agent:test-agent",
        limit=1,
    )

    assert len(batch) == 1
    assert batch[0]["candidate_id"] in {"cand-1", "cand-2"}
    assert batch[0]["content_preview"]
    direct_batch = collect_from_extract(
        store,
        proposed_scope="agent:test-agent",
        limit=1,
    )
    assert len(direct_batch) == 1


def test_apply_memory_consolidation_decisions_promotes_discards_and_defers() -> None:
    store = InMemoryMemoryStore()
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-promote",
            session_id="s1",
            proposed_scope="agent:test-agent",
            type="fact",
            title="Deploy region",
            content="Preferred deploy region is us-west-2.",
            confidence=0.8,
            source="validated",
        )
    )
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-discard",
            session_id="s1",
            proposed_scope="agent:test-agent",
            type="fact",
            title="Noisy preference",
            content="Sometimes maybe use blue theme.",
            confidence=0.2,
        )
    )
    store.candidate_put(
        MemoryCandidate(
            candidate_id="cand-defer",
            session_id="s1",
            proposed_scope="agent:test-agent",
            type="tool_outcome",
            title="Temporary error",
            content="The remote provider timed out once.",
            confidence=0.4,
        )
    )

    result = apply_memory_consolidation_decisions(
        MemoryService(store=store),
        decisions=[
            {
                "candidate_id": "cand-promote",
                "action": "promote",
                "reasoning": "Useful durable lesson.",
            },
            {
                "candidate_id": "cand-discard",
                "action": "discard",
                "reasoning": "Too weak to keep.",
            },
            {
                "candidate_id": "cand-defer",
                "action": "defer",
                "reasoning": "Need another confirming example.",
            },
        ],
        target_scope="agent:test-agent",
        selected_candidate_ids=["cand-promote", "cand-discard", "cand-defer"],
    )

    assert result["applied_count"] == 3
    assert result["promoted_count"] == 1
    assert result["discarded_count"] == 1
    assert result["deferred_count"] == 1
    assert store.candidate_get("cand-promote").status == "promoted"
    assert store.candidate_get("cand-discard").status == "rejected"
    assert store.candidate_get("cand-defer").status == "proposed"
    direct_result = apply_from_merge(
        MemoryService(store=store),
        decisions=[],
        target_scope="agent:test-agent",
        selected_candidate_ids=[],
    )
    assert direct_result["applied_count"] == 0


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_apply_memory_consolidation_decisions_rejects_unselected_and_duplicate_ids(
    backend: str,
    tmp_path: Path,
) -> None:
    store = (
        InMemoryMemoryStore()
        if backend == "memory"
        else SQLiteMemoryStore(tmp_path / "memory.db")
    )
    for candidate_id, proposed_scope in (
        ("selected", "agent:test-agent"),
        ("outside", "agent:test-agent"),
        ("other-agent", "agent:other-agent"),
        ("ambiguous", "agent:test-agent"),
    ):
        store.candidate_put(
            MemoryCandidate(
                candidate_id=candidate_id,
                session_id="s1",
                proposed_scope=proposed_scope,
                type="fact",
                title=candidate_id,
                content=f"{candidate_id} content",
                confidence=0.8,
            )
        )
    candidates_before = {
        candidate_id: store.candidate_get(candidate_id)
        for candidate_id in ("selected", "outside", "other-agent", "ambiguous")
    }

    result = apply_memory_consolidation_decisions(
        MemoryService(store=store),
        decisions=[
            {"candidate_id": "selected", "action": "defer"},
            {"candidate_id": "selected", "action": "discard"},
            {"candidate_id": "outside", "action": "promote"},
            {"candidate_id": "other-agent", "action": "discard"},
            {"candidate_id": "ambiguous", "action": "discard"},
        ],
        target_scope="agent:test-agent",
        selected_candidate_ids=["selected", "ambiguous", "ambiguous"],
    )

    assert result["applied_count"] == 0
    assert result["deferred_count"] == 0
    assert result["discarded_count"] == 0
    assert len(result["errors"]) == 5
    for candidate_id, candidate_before in candidates_before.items():
        assert store.candidate_get(candidate_id) == candidate_before
    assert store.list(ListQueryOptions(scopes=["agent:test-agent"])) == []


def test_consolidation_contract_types_are_importable() -> None:
    class _Coordinator:
        def run_extraction(
            self,
            session_id: str,
            agent_id: str,
            recent_rollout_limit: int,
        ) -> ExtractionPayload:
            return ExtractionPayload(
                session_id=session_id,
                agent_id=agent_id,
                evidence_window={"recent_rollout_limit": recent_rollout_limit},
            )

        def run_merge(
            self,
            payload: ExtractionPayload,
            consolidation_model_handle: RuntimeLLMHandle,
        ) -> MergeDecisions:
            return MergeDecisions(
                decisions=[
                    MergeDecision(
                        candidate_id="cand-1",
                        action="defer",
                        reasoning=f"model={consolidation_model_handle.model}",
                    )
                ],
                model_name=consolidation_model_handle.model,
            )

    config = ConsolidationConfig(consolidation_model="gpt-4.2-mini")
    coordinator: ConsolidationCoordinator = _Coordinator()
    payload = coordinator.run_extraction("session-1", "agent-1", 256)
    decisions = coordinator.run_merge(
        payload,
        RuntimeLLMHandle(name="openai", model="gpt-4.2-mini", client=object()),
    )

    assert config.recent_rollout_limit == 256
    assert config.consolidation_model == "gpt-4.2-mini"
    assert payload.evidence_window["recent_rollout_limit"] == 256
    assert decisions.model_name == "gpt-4.2-mini"
    assert decisions.decisions[0].action == "defer"
