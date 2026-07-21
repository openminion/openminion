from __future__ import annotations

from openminion.modules.brain import improvement as improvement_api
from openminion.modules.skill.learning import (
    WorkflowEvidenceBundle,
    WorkflowShapeMiner,
)


def test_explicit_structural_contracts_select_one_owner_without_prose_routing() -> None:
    bundles = [
        WorkflowEvidenceBundle(
            source_run_refs=[f"run-{index}"],
            tool_names=["exec"],
            command_fingerprints=["command-1"],
            validation_summary=summary,
            outcome="success",
            intent_category="task:cleanup",
            capability_category="capability:test",
            strategy_id="strategy:focused",
        )
        for index, summary in enumerate(
            (
                "remember this instruction as a skill",
                "different prose with the same structural workflow",
            ),
            start=1,
        )
    ]
    shapes = WorkflowShapeMiner().mine(bundles)
    assert len(shapes) == 1

    opportunity = improvement_api.InstructionOpportunity(
        opportunity_id="opp-1",
        source_kind="operator_signal",
        evidence_refs=["run:1"],
        target_hint="memory skill words do not select an owner",
    )
    assert opportunity.needs_authoring is True

    calls: list[dict] = []

    class MemoryService:
        def stage_candidate(self, **kwargs):
            calls.append(kwargs)
            return "memory-candidate-1"

    candidate = improvement_api.ImprovementCandidate(
        candidate_id="memory-1",
        target_type="memory",
        target_owner="openminion-memory",
        summary="turn this instruction into a skill",
        evidence_refs=["run:1"],
        semantic_author_source="operator",
    )
    result = improvement_api.stage_learning_memory_candidate(
        candidate,
        memory_service=MemoryService(),
        session_id="session-1",
        agent_id="agent-1",
    )

    assert result.status == "staged"
    assert len(calls) == 1
    assert calls[0]["record_type"] == "fact"


def test_rejected_memory_candidate_cannot_reenter_staging() -> None:
    class MemoryService:
        def stage_candidate(self, **kwargs):
            raise AssertionError(f"unexpected memory owner call: {kwargs}")

    candidate = improvement_api.ImprovementCandidate(
        candidate_id="memory-rejected",
        target_type="memory",
        target_owner="openminion-memory",
        summary="remember this",
        semantic_author_source="llm",
        state="rejected",
    )

    result = improvement_api.stage_learning_memory_candidate(
        candidate,
        memory_service=MemoryService(),
        session_id="session-1",
        agent_id="agent-1",
    )

    assert result.status == "skipped"
    assert result.reason_code == "candidate_state_not_stageable"


def test_public_improvement_surface_has_no_generic_semantic_router() -> None:
    assert not hasattr(improvement_api, "route_learning_observation")
