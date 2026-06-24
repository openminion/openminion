from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from openminion.base.time import utc_now_iso
from .base import SkillProposal, SkillProposalDraft
from .catalog import EmergentSkillCatalogAddition, apply_emergent_skill
from openminion.modules.skill.models import canonical_json
from .review import (
    SkillProposalCriterionDecision,
    SkillProposalReview,
    decide_skill_proposal,
)
from openminion.modules.skill.storage.base import SkillStore


PROPOSAL_QUEUE_STATE_PENDING = "pending"
PROPOSAL_QUEUE_STATE_REVIEWED = "reviewed"
PROPOSAL_QUEUE_STATE_APPLIED = "applied"

_VALID_QUEUE_STATES = frozenset(
    {
        PROPOSAL_QUEUE_STATE_PENDING,
        PROPOSAL_QUEUE_STATE_REVIEWED,
        PROPOSAL_QUEUE_STATE_APPLIED,
    }
)


class ProposalQueueError(ValueError):
    """Raised when a proposal-queue invariant is violated."""


def create_proposal(
    store: SkillStore,
    proposal: SkillProposal | Mapping[str, Any],
) -> dict[str, Any]:
    """Persist a proposal as ``pending``."""

    proposal_obj = (
        proposal
        if isinstance(proposal, SkillProposal)
        else SkillProposal.model_validate(proposal)
    )
    proposal_id = str(proposal_obj.proposal_id or "").strip()
    if not proposal_id:
        raise ProposalQueueError("proposal_id is required")
    proposed_at = str(proposal_obj.proposed_at or "").strip() or utc_now_iso()
    proposal_payload = proposal_obj.model_copy(
        update={"proposed_at": proposed_at}
    ).model_dump(mode="json")
    created_at = utc_now_iso()
    inserted = store.create_proposal(
        proposal_id=proposal_id,
        source_task_shape_ref=str(proposal_obj.source_task_shape_ref or ""),
        proposer_policy_id=str(proposal_obj.proposer_policy_id or ""),
        proposed_at=proposed_at,
        proposal_json=canonical_json(proposal_payload),
        created_at=created_at,
    )
    record = store.get_proposal(proposal_id=proposal_id)
    if record is None:
        raise ProposalQueueError(
            f"proposal not retrievable after create: {proposal_id!r}"
        )
    record["created_now"] = bool(inserted)
    return record


def list_proposals(
    store: SkillStore,
    *,
    queue_state: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if queue_state is not None and queue_state not in _VALID_QUEUE_STATES:
        raise ProposalQueueError(
            f"queue_state must be one of {sorted(_VALID_QUEUE_STATES)}; "
            f"got {queue_state!r}"
        )
    return store.list_proposals(queue_state=queue_state, limit=int(limit))


def get_proposal(
    store: SkillStore,
    *,
    proposal_id: str,
) -> dict[str, Any] | None:
    ref = str(proposal_id or "").strip()
    if not ref:
        raise ProposalQueueError("proposal_id is required")
    return store.get_proposal(proposal_id=ref)


def record_proposal_review(
    store: SkillStore,
    *,
    proposal_id: str,
    reviewer_id: str,
    review_policy_id: str,
    criterion_decisions: Iterable[SkillProposalCriterionDecision | Mapping[str, Any]],
) -> SkillProposalReview:
    """Review a persisted proposal with ``decide_skill_proposal()``."""

    record = get_proposal(store, proposal_id=proposal_id)
    if record is None:
        raise ProposalQueueError(f"proposal not found: {proposal_id!r}")

    proposal = SkillProposal.model_validate(record["proposal"])
    review = decide_skill_proposal(
        proposal,
        reviewer_id=reviewer_id,
        review_policy_id=review_policy_id,
        criterion_decisions=criterion_decisions,
    )
    store.record_proposal_review(
        proposal_id=str(proposal.proposal_id or ""),
        status=str(review.status),
        reviewer_id=str(review.reviewer_id),
        review_policy_id=str(review.review_policy_id or ""),
        decided_at=str(review.decided_at),
        review_json=canonical_json(review.model_dump(mode="json")),
        created_at=utc_now_iso(),
    )
    from openminion.modules.skill.suggestion import record_outcome

    record_outcome(store, proposal_id=str(proposal.proposal_id or ""), review=review)
    return review


def apply_proposal(
    store: SkillStore,
    *,
    proposal_id: str,
    current_catalog: Iterable[Any],
) -> EmergentSkillCatalogAddition:
    """Apply an accepted-review proposal with ``apply_emergent_skill()``."""

    record = get_proposal(store, proposal_id=proposal_id)
    if record is None:
        raise ProposalQueueError(f"proposal not found: {proposal_id!r}")
    queue_state = str(record.get("queue_state") or "")
    if queue_state == PROPOSAL_QUEUE_STATE_APPLIED:
        existing = record.get("applied_addition")
        if isinstance(existing, Mapping):
            return EmergentSkillCatalogAddition.model_validate(existing)
        raise ProposalQueueError(
            f"proposal already applied but addition payload missing: {proposal_id!r}"
        )
    if queue_state != PROPOSAL_QUEUE_STATE_REVIEWED:
        raise ProposalQueueError(
            "apply requires a recorded review with status='accepted'; "
            f"current queue_state={queue_state!r}"
        )
    review_payload = record.get("review")
    if not isinstance(review_payload, Mapping):
        raise ProposalQueueError(
            f"proposal has no recorded review payload: {proposal_id!r}"
        )
    review = SkillProposalReview.model_validate(review_payload)
    if review.status != "accepted":
        raise ProposalQueueError(
            f"apply requires accepted review; got status={review.status!r}"
        )
    proposal = SkillProposal.model_validate(record["proposal"])
    draft: SkillProposalDraft = proposal.proposed_skill_definition
    addition, _new_catalog = apply_emergent_skill(
        review,
        catalog=list(current_catalog or []),
        skill_definition=draft,
    )
    store.apply_proposal(
        proposal_id=str(proposal.proposal_id or ""),
        applied_at=utc_now_iso(),
        applied_addition_json=canonical_json(addition.model_dump(mode="json")),
    )
    return addition


__all__ = (
    "PROPOSAL_QUEUE_STATE_APPLIED",
    "PROPOSAL_QUEUE_STATE_PENDING",
    "PROPOSAL_QUEUE_STATE_REVIEWED",
    "ProposalQueueError",
    "apply_proposal",
    "create_proposal",
    "get_proposal",
    "list_proposals",
    "record_proposal_review",
)
