"""Stage workflow shapes through the existing skill proposal queue."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib import import_module
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.modules.skill.proposal import queue as proposal_queue
from openminion.modules.skill.proposal.base import (
    SkillProposal,
    SkillProposalDraft,
    propose_skills_from_task_shapes,
)
from openminion.modules.skill.storage.base import SkillStore
from openminion.modules.skill.models import stable_hash

from .runtime import list_runtime_workflow_shapes
from .shapes import WorkflowShape


class LearningProposalResult(BaseModel):
    """Result of staging one workflow shape as a reviewable skill proposal."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["staged", "skipped_duplicate", "not_ready"]
    shape_id: str
    candidate: dict[str, Any] | None = None
    proposal: SkillProposal | None = None
    queue_record: dict[str, Any] = Field(default_factory=dict)
    reason_code: str = ""


def _candidate_from_shape(shape: WorkflowShape, *, policy_id: str) -> dict[str, Any]:
    payload = _candidate_payload(shape, policy_id=policy_id)
    try:
        # Lazy import keeps skill learning from forming a brain<->skill module cycle.
        candidates_module = import_module(
            "openminion.modules.brain.runtime.improvement.candidates"
        )
    except ModuleNotFoundError:
        return payload
    candidate_cls = getattr(candidates_module, "ImprovementCandidate")
    return candidate_cls(**payload).model_dump(mode="json")


def _candidate_payload(shape: WorkflowShape, *, policy_id: str) -> dict[str, Any]:
    return {
        "candidate_id": shape.shape_id,
        "target_type": "skill",
        "target_owner": "openminion-skill",
        "summary": (
            "Learn workflow skill for "
            f"{shape.strategy_id}/{shape.capability_category}/{shape.intent_category}"
        ),
        "evidence_refs": list(shape.evidence_refs),
        "risk_level": shape.risk_level,
        "review_mode": "review_first",
        "replay_eval_requirements": [
            f"workflow_replay_passed:{shape.shape_id}",
            "operator_review_required",
        ],
        "state": "staged",
        "actor_id": "runtime",
        "source": f"workflow_learning:{policy_id}",
    }


def _proposal_with_shape_evidence(
    proposal: SkillProposal,
    shape: WorkflowShape,
) -> SkillProposal:
    draft = proposal.proposed_skill_definition
    verification_rules = list(draft.verification_rules)
    replay_rule = f"workflow_replay_passed:{shape.shape_id}"
    if replay_rule not in verification_rules:
        verification_rules.append(replay_rule)
    if "operator_review_required" not in verification_rules:
        verification_rules.append("operator_review_required")
    draft_payload = draft.model_dump(mode="json")
    draft_payload["verification_rules"] = verification_rules
    enriched_draft = SkillProposalDraft(**draft_payload)
    return proposal.model_copy(
        update={
            "proposed_skill_definition": enriched_draft,
            "evidence_refs": list(shape.evidence_refs),
            "requires_replay_proof": True,
        }
    )


def stage_shape_as_skill_proposal(
    shape: WorkflowShape,
    *,
    store: SkillStore,
    current_catalog: Iterable[Mapping[str, Any] | object],
    policy_id: str = "workflow_learning_review_first",
) -> LearningProposalResult:
    """Create an improvement candidate and persist a pending skill proposal."""

    if shape.success_count < 2 and shape.explicit_save_count < 1:
        return LearningProposalResult(
            status="not_ready",
            shape_id=shape.shape_id,
            reason_code="recurrence_or_save_signal_required",
        )
    proposals = propose_skills_from_task_shapes(
        [shape],
        current_catalog=current_catalog,
        policy_id=policy_id,
    )
    if not proposals:
        return LearningProposalResult(
            status="skipped_duplicate",
            shape_id=shape.shape_id,
            candidate=_candidate_from_shape(shape, policy_id=policy_id),
            reason_code="duplicate_catalog_signature",
        )
    proposal = _proposal_with_shape_evidence(proposals[0], shape)
    queue_record = proposal_queue.create_proposal(store, proposal)
    return LearningProposalResult(
        status="staged",
        shape_id=shape.shape_id,
        candidate=_candidate_from_shape(shape, policy_id=policy_id),
        proposal=proposal,
        queue_record=dict(queue_record),
    )


def stage_authored_skill_proposal(
    *,
    store: SkillStore,
    agent_id: str,
    shape_id: str,
    draft: SkillProposalDraft,
) -> LearningProposalResult:
    agent_id = agent_id.strip()
    shapes = list_runtime_workflow_shapes(store, agent_id=agent_id)
    shape = next((item for item in shapes if item.shape_id == shape_id), None)
    if shape is None:
        raise ValueError("shape is not stored for the selected agent")
    if shape.success_count < 2:
        return LearningProposalResult(
            status="not_ready",
            shape_id=shape.shape_id,
            reason_code="recurrence_required",
        )
    if draft.procedure is None:
        raise ValueError("authored proposal requires procedure content")
    if not draft.name.strip() or not draft.short_description.strip():
        raise ValueError("authored proposal requires name and short_description")
    if draft.risk_class != shape.risk_level:
        raise ValueError("authored risk must equal stored shape risk")
    if not set(draft.tools).issubset(shape.tool_names):
        raise ValueError("authored tools must be a subset of stored shape tools")
    policy_id = "workflow_learning_authored_v1"
    proposal = SkillProposal(
        proposal_id=stable_hash(
            {
                "proposer_policy_id": policy_id,
                "agent_id": agent_id,
                "source_task_shape_ref": shape.task_shape_ref,
                "draft": draft.model_dump(mode="json"),
            }
        ),
        source_task_shape_ref=shape.task_shape_ref,
        proposed_skill_definition=draft,
        evidence_refs=list(shape.evidence_refs),
        proposer_policy_id=policy_id,
        requires_replay_proof=True,
        agent_id=agent_id,
    )
    queue_record = proposal_queue.create_proposal(store, proposal)
    return LearningProposalResult(
        status="staged",
        shape_id=shape.shape_id,
        proposal=SkillProposal.model_validate(queue_record["proposal"]),
        queue_record=dict(queue_record),
    )


__all__ = (
    "LearningProposalResult",
    "stage_authored_skill_proposal",
    "stage_shape_as_skill_proposal",
)
