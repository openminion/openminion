from collections.abc import Iterable, Mapping
from typing import Any

from openminion.base.time import utc_now_iso
from .base import SkillProposal, SkillProposalDraft
from .catalog import EmergentSkillCatalogAddition, apply_emergent_skill
from openminion.modules.skill.models import SkillPackage, canonical_json
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


class ProposalNotFoundError(ProposalQueueError):
    """Raised when a proposal id has no stored record."""


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
    inserted = store.create_proposal(
        proposal_id=proposal_id,
        source_task_shape_ref=str(proposal_obj.source_task_shape_ref or ""),
        proposer_policy_id=str(proposal_obj.proposer_policy_id or ""),
        proposed_at=proposed_at,
        proposal_json=canonical_json(proposal_payload),
        created_at=utc_now_iso(),
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
    return store.list_proposals(queue_state=queue_state, limit=limit)


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
        raise ProposalNotFoundError(f"proposal not found: {proposal_id!r}")

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
    replay_proof: object | None = None,
) -> EmergentSkillCatalogAddition:
    """Apply an accepted-review proposal with ``apply_emergent_skill()``."""

    record = get_proposal(store, proposal_id=proposal_id)
    if record is None:
        raise ProposalNotFoundError(f"proposal not found: {proposal_id!r}")
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
    from openminion.modules.skill.learning.replay import (
        ReplayProof,
        require_replay_passed,
    )

    retained = record.get("replay_proof")
    bound_replay_proof = (
        ReplayProof.model_validate(retained) if isinstance(retained, Mapping) else None
    )
    if proposal.requires_replay_proof and bound_replay_proof is None:
        raise ProposalQueueError("learned proposal requires retained replay proof")
    if bound_replay_proof is not None:
        require_replay_passed(bound_replay_proof, proposal)
    if replay_proof is not None:
        supplied = ReplayProof.model_validate(replay_proof)
        if bound_replay_proof is None or supplied != bound_replay_proof:
            raise ProposalQueueError(
                "replay proof does not match retained evaluator result"
            )
    draft: SkillProposalDraft = proposal.proposed_skill_definition
    addition, new_catalog = apply_emergent_skill(
        review,
        catalog=list(current_catalog),
        skill_definition=draft,
    )
    package = new_catalog[-1]
    _persist_pending_package(store, package)
    if bound_replay_proof is not None:
        addition = addition.model_copy(
            update={"replay_proof": bound_replay_proof.model_dump(mode="json")}
        )
    store.apply_proposal(
        proposal_id=str(proposal.proposal_id or ""),
        applied_at=utc_now_iso(),
        applied_addition_json=canonical_json(addition.model_dump(mode="json")),
    )
    return addition


def record_replay_proof(
    store: SkillStore,
    *,
    proposal_id: str,
    result_ref: str,
    artifactctl: Any,
) -> dict[str, Any]:
    record = get_proposal(store, proposal_id=proposal_id)
    if record is None:
        raise ProposalNotFoundError(f"proposal not found: {proposal_id!r}")
    from openminion.modules.skill.learning.replay import (
        ReplayEvaluationResult,
        require_replay_passed,
    )

    meta = artifactctl.get(result_ref)
    if getattr(meta, "deleted_at", None):
        raise ProposalQueueError("replay evaluator result is deleted")
    evaluation = ReplayEvaluationResult.model_validate_json(
        artifactctl.read_bytes(result_ref)
    )
    if str(getattr(meta, "agent_id", "") or "") != evaluation.evaluator_id:
        raise ProposalQueueError("replay evaluator provenance mismatch")
    proof = evaluation.to_proof(result_ref=meta.to_ref().ref)
    proposal = SkillProposal.model_validate(record["proposal"])
    require_replay_passed(proof, proposal)
    store.record_proposal_replay_proof(
        proposal_id=proposal_id,
        replay_proof_json=canonical_json(proof.model_dump(mode="json")),
        recorded_at=utc_now_iso(),
    )
    return dict(proof.model_dump(mode="json"))


def _persist_pending_package(store: SkillStore, package: SkillPackage) -> None:
    store.upsert_skill(
        skill_id=package.skill_id,
        name=package.name,
        status=package.status,
        scope=package.scope,
        agent_id=package.agent_id,
        ts=package.updated_at,
    )
    store.insert_skill_version(
        skill_id=package.skill_id,
        version_hash=package.version_hash,
        source_artifact_ref=package.source_artifact_ref,
        package_json=canonical_json(package.to_dict()),
        created_at=package.created_at,
        content_fingerprint=package.to_content_fingerprint(),
    )
    store.upsert_skill_index(
        skill_id=package.skill_id,
        version_hash=package.version_hash,
        tags_json=canonical_json(package.tags),
        tools_json=canonical_json(package.tools),
        keywords_json=canonical_json(package.tags),
        applies_to_json=canonical_json(package.applies_to),
    )
    store.stage_skill_version(
        skill_id=package.skill_id,
        version_hash=package.version_hash,
        content_fingerprint=package.to_content_fingerprint(),
        authority_class="runtime_untrusted",
        created_at=package.created_at,
    )


__all__ = (
    "PROPOSAL_QUEUE_STATE_APPLIED",
    "PROPOSAL_QUEUE_STATE_PENDING",
    "PROPOSAL_QUEUE_STATE_REVIEWED",
    "ProposalNotFoundError",
    "ProposalQueueError",
    "apply_proposal",
    "create_proposal",
    "get_proposal",
    "list_proposals",
    "record_proposal_review",
    "record_replay_proof",
)
