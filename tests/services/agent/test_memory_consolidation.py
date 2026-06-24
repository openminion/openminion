from __future__ import annotations

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
        store,
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
    )

    assert result["applied_count"] == 3
    assert result["promoted_count"] == 1
    assert result["discarded_count"] == 1
    assert result["deferred_count"] == 1
    assert store.candidate_get("cand-promote").status == "promoted"
    assert store.candidate_get("cand-discard").status == "rejected"
    assert store.candidate_get("cand-defer").status == "proposed"
    direct_result = apply_from_merge(
        store,
        decisions=[],
        target_scope="agent:test-agent",
    )
    assert direct_result["applied_count"] == 0


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
