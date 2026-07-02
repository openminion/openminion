from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.modules.brain.runtime.improvement.candidates import (
    IMPROVEMENT_CANDIDATE_TARGETS,
    ImprovementCandidate,
    ImprovementCandidateRegistry,
    stage_candidate_with_default_owners,
    stage_candidate_with_owner,
)


def _candidate(target_type: str = "memory", *, evidence: list[str] | None = None) -> dict:
    return {
        "candidate_id": f"cand-{target_type}",
        "target_type": target_type,
        "target_owner": f"{target_type}_owner",
        "summary": f"Improve {target_type}",
        "evidence_refs": list(evidence or []),
    }


def test_improvement_candidate_accepts_every_closed_target_type() -> None:
    for target_type in IMPROVEMENT_CANDIDATE_TARGETS:
        candidate = ImprovementCandidate.model_validate(_candidate(target_type))

        assert candidate.target_type == target_type
        assert candidate.state == "staged"


def test_improvement_candidate_rejects_unknown_target_type() -> None:
    with pytest.raises(ValidationError):
        ImprovementCandidate.model_validate(_candidate("runtime_guess"))


def test_improvement_candidate_requires_evidence_for_promotion_and_rollback() -> None:
    candidate = ImprovementCandidate.model_validate(_candidate())

    with pytest.raises(ValueError, match="promotion_or_rollback_requires_evidence"):
        candidate.transition("promoted")
    with pytest.raises(ValueError, match="promotion_or_rollback_requires_evidence"):
        candidate.transition("rolled_back")


def test_improvement_candidate_registry_tracks_state_transitions() -> None:
    registry = ImprovementCandidateRegistry()
    staged = registry.stage(ImprovementCandidate.model_validate(_candidate(evidence=["trace:1"])))

    promoted = registry.transition(staged.candidate_id, "promoted")

    assert promoted.state == "promoted"
    assert registry.get(staged.candidate_id) == promoted
    assert registry.readout()[0]["state"] == "promoted"


def test_stage_candidate_with_owner_calls_matching_adapter() -> None:
    calls: list[str] = []
    candidate = ImprovementCandidate.model_validate(_candidate("skill"))

    result = stage_candidate_with_owner(
        candidate,
        owner_stage_fns={
            "skill": lambda item: calls.append(item.candidate_id) or {"proposal_id": "p1"},
        },
    )

    assert result.status == "staged"
    assert result.owner_result == {"proposal_id": "p1"}
    assert calls == ["cand-skill"]


def test_stage_candidate_with_owner_reports_unsupported_target() -> None:
    candidate = ImprovementCandidate.model_validate(_candidate("docs"))

    result = stage_candidate_with_owner(candidate, owner_stage_fns={})

    assert result.status == "unsupported"
    assert result.reason_code == "unsupported_target_owner"


def test_default_owner_adapter_stages_memory_candidate_through_memory_service() -> None:
    calls: list[dict] = []

    class MemoryService:
        def stage_candidate(self, **kwargs):
            calls.append(kwargs)
            return "mem-candidate-1"

    result = stage_candidate_with_default_owners(
        ImprovementCandidate.model_validate(_candidate("memory", evidence=["trace:1"])),
        memory_service=MemoryService(),
        session_id="s1",
        agent_id="mini",
        trace_id="trace:1",
    )

    assert result.status == "staged"
    assert result.owner_result["candidate_ids"] == ["mem-candidate-1"]
    assert calls[0]["scope"] == "agent:mini"
    assert calls[0]["record_type"] == "fact"
    assert "self_improvement" in calls[0]["tags"]


def test_default_owner_adapter_stages_skill_candidate_through_proposal_queue() -> None:
    class SkillStore:
        def __init__(self) -> None:
            self.created: list[dict] = []

        def create_proposal(self, **kwargs) -> bool:
            self.created.append(kwargs)
            return True

        def get_proposal(self, *, proposal_id: str) -> dict:
            return {
                "proposal_id": proposal_id,
                "proposal": {
                    "proposal_id": proposal_id,
                    "source_task_shape_ref": "cand-skill",
                    "proposed_skill_definition": {
                        "name": "improve-skill",
                        "display_name": "Improve skill",
                        "short_description": "Improve skill",
                    },
                    "evidence_refs": ["trace:1"],
                    "proposer_policy_id": "review_first",
                    "proposed_at": "",
                },
            }

    store = SkillStore()

    result = stage_candidate_with_default_owners(
        ImprovementCandidate.model_validate(_candidate("skill", evidence=["trace:1"])),
        skill_store=store,
    )

    assert result.status == "staged"
    assert result.owner_result["proposal_id"] == "rsai-cand-skill"
    assert store.created[0]["proposal_id"] == "rsai-cand-skill"


def test_default_owner_adapter_stages_docs_candidate_through_docs_owner() -> None:
    class DocsOwner:
        def __init__(self) -> None:
            self.candidate_ids: list[str] = []

        def record_tracker_candidate(self, candidate: ImprovementCandidate) -> dict:
            self.candidate_ids.append(candidate.candidate_id)
            return {"tracker_ref": "docs/trackers/wip/example.md"}

    owner = DocsOwner()

    result = stage_candidate_with_default_owners(
        ImprovementCandidate.model_validate(_candidate("docs")),
        docs_owner=owner,
    )

    assert result.status == "staged"
    assert result.owner_result["tracker_ref"] == "docs/trackers/wip/example.md"
    assert owner.candidate_ids == ["cand-docs"]


def test_default_owner_adapter_keeps_unsupported_targets_visible() -> None:
    result = stage_candidate_with_default_owners(
        ImprovementCandidate.model_validate(_candidate("workflow")),
        memory_service=object(),
    )

    assert result.status == "unsupported"
    assert result.reason_code == "unsupported_target_owner"
