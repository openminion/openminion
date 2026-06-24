from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from openminion.base.time import utc_now_iso
from .base import SkillProposal

ReviewStatus = Literal["accepted", "rejected", "deferred"]
_RUNTIME_REVIEWER_IDS = frozenset({"runtime", "system", "auto", "automatic", "self"})


class SkillProposalCriterionDecision(BaseModel):
    """One criterion-level review decision."""

    model_config = ConfigDict(extra="forbid")

    criterion_id: str
    status: ReviewStatus
    comment: str


class SkillProposalReview(BaseModel):
    """Typed review decision for one proposal."""

    model_config = ConfigDict(extra="forbid")

    proposal_ref: str
    status: ReviewStatus
    reviewer_id: str
    review_policy_id: str = ""
    decided_at: str
    reviewer_notes: list[SkillProposalCriterionDecision] = Field(default_factory=list)


def _proposal_field(proposal: Any, field: str) -> Any:
    if isinstance(proposal, Mapping):
        return proposal.get(field)
    return getattr(proposal, field, None)


def _memory_backend(memory_api: Any) -> Any | None:
    if memory_api is None:
        return None
    backend = getattr(memory_api, "_backend", None)
    if backend is not None:
        return backend
    store = getattr(memory_api, "store", None)
    if store is not None:
        return store
    return memory_api


def _normalize_reviewer_id(reviewer_id: str) -> str:
    normalized = str(reviewer_id or "").strip()
    if not normalized:
        raise ValueError("reviewer_id is required")
    if normalized.lower() in _RUNTIME_REVIEWER_IDS:
        raise ValueError("reviewer_id must be operator-supplied")
    return normalized


def _normalize_criterion_decisions(
    criterion_decisions: Iterable[Any],
) -> list[SkillProposalCriterionDecision]:
    decisions: list[SkillProposalCriterionDecision] = []
    for item in criterion_decisions or []:
        decision = (
            item
            if isinstance(item, SkillProposalCriterionDecision)
            else SkillProposalCriterionDecision.model_validate(item)
        )
        criterion_id = str(decision.criterion_id or "").strip()
        comment = str(decision.comment or "").strip()
        if not criterion_id:
            raise ValueError("criterion_id is required")
        if not comment:
            raise ValueError("criterion comment is required")
        decisions.append(
            SkillProposalCriterionDecision(
                criterion_id=criterion_id,
                status=decision.status,
                comment=comment,
            )
        )
    if not decisions:
        raise ValueError("criterion_decisions are required")
    return decisions


def _rollup_status(
    decisions: Iterable[SkillProposalCriterionDecision],
) -> ReviewStatus:
    statuses = {decision.status for decision in decisions}
    if "rejected" in statuses:
        return "rejected"
    if "deferred" in statuses:
        return "deferred"
    return "accepted"


def decide_skill_proposal(
    proposal: SkillProposal | Mapping[str, Any],
    *,
    reviewer_id: str,
    review_policy_id: str,
    criterion_decisions: Iterable[SkillProposalCriterionDecision | Mapping[str, Any]],
) -> SkillProposalReview:
    """Produce one review decision from operator-supplied inputs."""

    proposal_obj = (
        proposal
        if isinstance(proposal, SkillProposal)
        else SkillProposal.model_validate(proposal)
    )
    normalized_reviewer_id = _normalize_reviewer_id(reviewer_id)
    normalized_decisions = _normalize_criterion_decisions(criterion_decisions)
    return SkillProposalReview(
        proposal_ref=str(proposal_obj.proposal_id or "").strip(),
        status=_rollup_status(normalized_decisions),
        reviewer_id=normalized_reviewer_id,
        review_policy_id=str(review_policy_id or "").strip(),
        decided_at=utc_now_iso(),
        reviewer_notes=normalized_decisions,
    )


def list_pending_proposals(memory_api: Any, *, limit: int) -> list[SkillProposal]:
    """List proposals awaiting review from a memory-backed surface."""

    backend = _memory_backend(memory_api)
    if backend is None:
        return []
    lister = getattr(backend, "list_pending_skill_proposals", None)
    if not callable(lister):
        lister = getattr(backend, "list_pending_proposals", None)
    if not callable(lister):
        return []
    try:
        items = list(lister(limit=max(1, int(limit))))
    except Exception:
        return []
    proposals: list[SkillProposal] = []
    for item in items:
        try:
            proposal = (
                item
                if isinstance(item, SkillProposal)
                else SkillProposal.model_validate(item)
            )
        except Exception:
            continue
        proposal_ref = str(_proposal_field(proposal, "proposal_id") or "").strip()
        if not proposal_ref:
            continue
        proposals.append(proposal)
    proposals.sort(key=lambda item: item.proposal_id)
    return proposals[: max(1, int(limit))]


__all__ = (
    "SkillProposalCriterionDecision",
    "SkillProposalReview",
    "decide_skill_proposal",
    "list_pending_proposals",
)
