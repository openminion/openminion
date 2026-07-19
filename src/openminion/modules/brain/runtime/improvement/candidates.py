"""Generic improvement candidate contracts layered on BSIL."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from functools import partial
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ImprovementCandidateTarget = Literal[
    "memory",
    "skill",
    "instruction",
    "tool_policy",
    "retrieval_policy",
    "threshold",
    "workflow",
    "context_policy",
    "docs",
]
ImprovementCandidateState = Literal[
    "staged",
    "under_review",
    "suppressed",
    "promoted",
    "rolled_back",
    "rejected",
]
ImprovementCandidateRisk = Literal["low", "medium", "high"]
ImprovementCandidateReviewMode = Literal["review_first", "manual", "automatic"]

IMPROVEMENT_CANDIDATE_TARGETS: tuple[ImprovementCandidateTarget, ...] = (
    "memory",
    "skill",
    "instruction",
    "tool_policy",
    "retrieval_policy",
    "threshold",
    "workflow",
    "context_policy",
    "docs",
)
IMPROVEMENT_CANDIDATE_STATES: tuple[ImprovementCandidateState, ...] = (
    "staged",
    "under_review",
    "suppressed",
    "promoted",
    "rolled_back",
    "rejected",
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ImprovementCandidate(BaseModel):
    """One reviewable proposed future change outside live chat mutation."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    target_type: ImprovementCandidateTarget
    target_owner: str
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    risk_level: ImprovementCandidateRisk = "medium"
    review_mode: ImprovementCandidateReviewMode = "review_first"
    replay_eval_requirements: list[str] = Field(default_factory=list)
    state: ImprovementCandidateState = "staged"
    created_at: str = Field(default_factory=_utc_now_iso)
    updated_at: str = Field(default_factory=_utc_now_iso)
    actor_id: str = "runtime"
    source: str = "self_improvement"

    def transition(self, state: ImprovementCandidateState) -> "ImprovementCandidate":
        if state in {"promoted", "rolled_back"} and not self.evidence_refs:
            raise ValueError("promotion_or_rollback_requires_evidence")
        return self.model_copy(update={"state": state, "updated_at": _utc_now_iso()})


class ImprovementCandidateStageResult(BaseModel):
    """Result of handing a generic candidate to its owner."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    target_type: ImprovementCandidateTarget
    status: Literal["staged", "unsupported", "skipped"]
    owner_result: dict[str, Any] = Field(default_factory=dict)
    reason_code: str = ""


OwnerStageFn = Callable[[ImprovementCandidate], Mapping[str, Any] | None]


class ImprovementCandidateRegistry(BaseModel):
    """Small in-memory registry for candidate readout/tests."""

    model_config = ConfigDict(extra="forbid")

    candidates: dict[str, ImprovementCandidate] = Field(default_factory=dict)

    def stage(self, candidate: ImprovementCandidate) -> ImprovementCandidate:
        self.candidates[candidate.candidate_id] = candidate
        return candidate

    def get(self, candidate_id: str) -> ImprovementCandidate | None:
        return self.candidates.get(str(candidate_id or "").strip())

    def transition(
        self,
        candidate_id: str,
        state: ImprovementCandidateState,
    ) -> ImprovementCandidate:
        current = self.get(candidate_id)
        if current is None:
            raise KeyError(str(candidate_id or "").strip())
        updated = current.transition(state)
        self.candidates[updated.candidate_id] = updated
        return updated

    def readout(self) -> list[dict[str, Any]]:
        return [
            candidate.model_dump(mode="json")
            for candidate in sorted(
                self.candidates.values(),
                key=lambda item: (item.target_type, item.candidate_id),
            )
        ]


def stage_candidate_with_owner(
    candidate: ImprovementCandidate | Mapping[str, Any],
    *,
    owner_stage_fns: Mapping[ImprovementCandidateTarget, OwnerStageFn],
) -> ImprovementCandidateStageResult:
    """Stage one candidate through its owning module/service adapter."""

    candidate_obj = (
        candidate
        if isinstance(candidate, ImprovementCandidate)
        else ImprovementCandidate.model_validate(candidate)
    )
    stage_fn = owner_stage_fns.get(candidate_obj.target_type)
    if stage_fn is None:
        return ImprovementCandidateStageResult(
            candidate_id=candidate_obj.candidate_id,
            target_type=candidate_obj.target_type,
            status="unsupported",
            reason_code="unsupported_target_owner",
        )
    result = stage_fn(candidate_obj)
    return ImprovementCandidateStageResult(
        candidate_id=candidate_obj.candidate_id,
        target_type=candidate_obj.target_type,
        status="staged",
        owner_result=dict(result or {}),
    )


def build_owner_stage_fns(
    *,
    memory_service: Any | None = None,
    skill_store: Any | None = None,
    docs_owner: Any | None = None,
    instruction_store: Any | None = None,
    session_id: str = "",
    agent_id: str = "",
    trace_id: str | None = None,
) -> dict[ImprovementCandidateTarget, OwnerStageFn]:
    """Build owner adapters for stageable generic candidate targets."""

    stage_fns: dict[ImprovementCandidateTarget, OwnerStageFn] = {}
    if memory_service is not None:
        stage_fns["memory"] = partial(
            stage_memory_candidate,
            memory_service=memory_service,
            session_id=session_id,
            agent_id=agent_id,
            trace_id=trace_id,
        )
    if skill_store is not None:
        stage_fns["skill"] = partial(
            stage_skill_candidate,
            skill_store=skill_store,
        )
    if docs_owner is not None:
        stage_fns["docs"] = partial(
            stage_docs_candidate,
            docs_owner=docs_owner,
        )
    if instruction_store is not None:
        stage_fns["instruction"] = partial(
            stage_instruction_candidate,
            instruction_store=instruction_store,
        )
    return stage_fns


def stage_memory_candidate(
    candidate: ImprovementCandidate,
    *,
    memory_service: Any,
    session_id: str,
    agent_id: str,
    trace_id: str | None = None,
) -> Mapping[str, Any]:
    """Stage a memory-target candidate through the memory staging owner."""

    from openminion.modules.memory.runtime.staging import (
        ExtractedCandidateDTO,
        stage_extracted_candidates,
    )

    result = stage_extracted_candidates(
        memory_service=memory_service,
        session_id=str(session_id or ""),
        agent_id=str(agent_id or ""),
        trace_id=trace_id,
        candidates=[
            ExtractedCandidateDTO(
                kind="fact",
                normalized_key=f"improvement_candidate:{candidate.candidate_id}",
                title=candidate.summary,
                content=_candidate_content(candidate),
                tags=tuple(_generic_candidate_tags(candidate)),
            )
        ],
    )
    return {
        "candidate_ids": list(result.candidate_ids),
        "staged_count": result.staged_count,
        "skipped": [dict(item) for item in result.skipped],
    }


def stage_skill_candidate(
    candidate: ImprovementCandidate,
    *,
    skill_store: Any,
) -> Mapping[str, Any]:
    """Stage a skill-target candidate through the skill proposal queue owner."""

    from openminion.modules.skill.models import slugify
    from openminion.modules.skill.proposal.base import (
        SkillProposal,
        SkillProposalDraft,
    )
    from openminion.modules.skill.proposal.queue import create_proposal

    skill_name = slugify(candidate.summary, fallback=candidate.candidate_id)
    proposal = SkillProposal(
        proposal_id=f"rsai-{candidate.candidate_id}",
        source_task_shape_ref=candidate.candidate_id,
        proposed_skill_definition=SkillProposalDraft(
            name=skill_name,
            display_name=candidate.summary,
            short_description=candidate.summary,
            tags=_generic_candidate_tags(candidate),
            verification_rules=list(candidate.replay_eval_requirements),
        ),
        evidence_refs=list(candidate.evidence_refs),
        proposer_policy_id=candidate.review_mode,
    )
    return create_proposal(skill_store, proposal)


def stage_docs_candidate(
    candidate: ImprovementCandidate,
    *,
    docs_owner: Any,
) -> Mapping[str, Any]:
    """Hand docs-target candidates to the docs/tracker owner surface."""

    for method_name in (
        "stage_docs_candidate",
        "record_tracker_candidate",
        "stage_candidate",
    ):
        method = getattr(docs_owner, method_name, None)
        if callable(method):
            result = method(candidate)
            return dict(result or {})
    return {
        "status": "unsupported",
        "reason_code": "docs_owner_stage_unavailable",
    }


def stage_instruction_candidate(
    candidate: ImprovementCandidate,
    *,
    instruction_store: Any,
) -> Mapping[str, Any]:
    """Hand instruction candidates to the durable instruction proposal owner."""

    for method_name in ("stage_instruction_candidate", "stage_candidate"):
        method = getattr(instruction_store, method_name, None)
        if callable(method):
            result = method(candidate)
            return dict(result or {})
    return {
        "status": "unsupported",
        "reason_code": "instruction_owner_stage_unavailable",
    }


def stage_candidate_with_default_owners(
    candidate: ImprovementCandidate | Mapping[str, Any],
    *,
    memory_service: Any | None = None,
    skill_store: Any | None = None,
    docs_owner: Any | None = None,
    instruction_store: Any | None = None,
    session_id: str = "",
    agent_id: str = "",
    trace_id: str | None = None,
) -> ImprovementCandidateStageResult:
    """Stage a candidate through the built-in owner adapters."""

    return stage_candidate_with_owner(
        candidate,
        owner_stage_fns=build_owner_stage_fns(
            memory_service=memory_service,
            skill_store=skill_store,
            docs_owner=docs_owner,
            instruction_store=instruction_store,
            session_id=session_id,
            agent_id=agent_id,
            trace_id=trace_id,
        ),
    )


def _candidate_content(candidate: ImprovementCandidate) -> str:
    return json.dumps(candidate.model_dump(mode="json"), sort_keys=True)


def _generic_candidate_tags(candidate: ImprovementCandidate) -> list[str]:
    tags = ["self_improvement", f"target:{candidate.target_type}"]
    for ref in candidate.evidence_refs:
        tag = f"evidence:{ref}"
        if tag not in tags:
            tags.append(tag)
    return tags


__all__ = [
    "IMPROVEMENT_CANDIDATE_STATES",
    "IMPROVEMENT_CANDIDATE_TARGETS",
    "ImprovementCandidate",
    "ImprovementCandidateRegistry",
    "ImprovementCandidateReviewMode",
    "ImprovementCandidateRisk",
    "ImprovementCandidateStageResult",
    "ImprovementCandidateState",
    "ImprovementCandidateTarget",
    "build_owner_stage_fns",
    "stage_candidate_with_default_owners",
    "stage_candidate_with_owner",
    "stage_docs_candidate",
    "stage_instruction_candidate",
    "stage_memory_candidate",
    "stage_skill_candidate",
]
