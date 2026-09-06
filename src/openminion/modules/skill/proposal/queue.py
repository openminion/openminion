from collections.abc import Iterable, Mapping
from typing import Any, TYPE_CHECKING

from openminion.base.time import utc_now_iso
from openminion.modules.skill.constants import (
    SKILL_SOURCE_OPERATOR_DECLARED,
    SKILL_STATUS_VERIFIED,
)
from openminion.modules.skill.interfaces import (
    SkillIngestAuthority,
    SkillVerificationEvidence,
)
from openminion.modules.skill.models import canonical_json
from openminion.modules.skill.storage.base import SkillStore

from .base import SkillProposal
from .catalog import EmergentSkillCatalogAddition
from .review import (
    SkillProposalCriterionDecision,
    SkillProposalReview,
    decide_skill_proposal,
)

if TYPE_CHECKING:
    from openminion.modules.skill.runtime.skill import Skill


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


def record_proposal_verification(
    store: SkillStore,
    *,
    proposal_id: str,
    operator_id: str,
    evidence: SkillVerificationEvidence,
) -> SkillVerificationEvidence:
    """Attach local-operator verification evidence to an accepted proposal."""

    record = get_proposal(store, proposal_id=proposal_id)
    if record is None:
        raise ProposalNotFoundError(f"proposal not found: {proposal_id!r}")
    if record.get("queue_state") != PROPOSAL_QUEUE_STATE_REVIEWED:
        raise ProposalQueueError("proposal verification requires a recorded review")
    if record.get("review_status") != "accepted":
        raise ProposalQueueError("proposal verification requires an accepted review")
    if str(record.get("reviewer_id") or "") != str(operator_id or ""):
        raise ProposalQueueError("verifying operator must match the accepted reviewer")
    payload = {
        "check": evidence.check,
        "result": evidence.result,
        "evidence_ref": evidence.evidence_ref,
        "reviewer_id": operator_id,
    }
    store.record_proposal_verification(
        proposal_id=proposal_id,
        verification_evidence_json=canonical_json(payload),
        updated_at=utc_now_iso(),
    )
    return evidence


def apply_proposal(
    skill_runtime: "Skill",
    *,
    proposal_id: str,
    authority: SkillIngestAuthority,
) -> EmergentSkillCatalogAddition:
    """Admit authoritative proposal Markdown through the existing Skill runtime."""

    if authority.authority_class != "local_operator" or not authority.principal_id:
        raise ProposalQueueError("proposal apply requires local operator authority")
    store: SkillStore = skill_runtime.store
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
    proposal, review, verification = _validated_application_inputs(
        record,
        proposal_id=proposal_id,
        operator_id=authority.principal_id,
    )
    markdown = str(proposal.skill_markdown or "")
    if not markdown.strip():
        raise ProposalQueueError("proposal has no authoritative skill Markdown")

    candidate, _warnings = skill_runtime._build_package(
        markdown=markdown,
        explicit_name=proposal.proposed_skill_definition.name,
        source_artifact_ref=f"proposal:{proposal.proposal_id}",
        scope="global",
        agent_id=None,
        bundle_root=None,
        trust=None,
        remote_source=False,
        authority=authority,
    )
    active_rows = skill_runtime.list_skills({}) or []
    collisions = [
        row
        for row in active_rows
        if str(row.get("skill_id") or "") == candidate.skill_id
        or str(row.get("name") or "") == candidate.name
    ]
    if collisions:
        active = skill_runtime.get_skill(str(collisions[0]["skill_id"]))
        if active.to_content_fingerprint() != candidate.to_content_fingerprint():
            raise ProposalQueueError("proposal conflicts with an active skill identity")
        skill_id = active.skill_id
        version_hash = active.version_hash
    else:
        skill_id, version_hash, _warnings = skill_runtime.ingest_text(
            name=candidate.name,
            markdown=markdown,
            scope="global",
            authority=authority,
        )
        skill_runtime.admit_skill_version(
            skill_id=skill_id,
            version_hash=version_hash,
            expected_active_version_hash=None,
            target_status=SKILL_STATUS_VERIFIED,
            reason=f"accepted skill proposal {proposal.proposal_id}",
            authority=authority,
            verification_evidence=verification,
        )
    visible = skill_runtime.get_skill(skill_id)
    if visible.version_hash != version_hash:
        raise ProposalQueueError("admitted proposal version is not catalog-visible")
    addition = EmergentSkillCatalogAddition(
        review_ref=review.proposal_ref,
        added_skill_id=skill_id,
        source_field=SKILL_SOURCE_OPERATOR_DECLARED,
        added_at=utc_now_iso(),
        added_by=authority.principal_id,
        version_hash=version_hash,
    )
    store.apply_proposal(
        proposal_id=str(proposal.proposal_id or ""),
        applied_at=utc_now_iso(),
        applied_addition_json=canonical_json(addition.model_dump(mode="json")),
    )
    return addition


def _validated_application_inputs(
    record: Mapping[str, Any],
    *,
    proposal_id: str,
    operator_id: str,
) -> tuple[SkillProposal, SkillProposalReview, SkillVerificationEvidence]:
    queue_state = str(record.get("queue_state") or "")
    if queue_state != PROPOSAL_QUEUE_STATE_REVIEWED:
        raise ProposalQueueError(
            "apply requires a recorded review with status='accepted'; "
            f"current queue_state={record.get('queue_state')!r}"
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
    if review.reviewer_id != operator_id:
        raise ProposalQueueError("applying operator must match the accepted reviewer")
    verification_payload = record.get("verification_evidence")
    if not isinstance(verification_payload, Mapping):
        raise ProposalQueueError("apply requires recorded verification evidence")
    if str(verification_payload.get("reviewer_id") or "") != operator_id:
        raise ProposalQueueError("verification operator must match the applying operator")
    if str(verification_payload.get("result") or "") != "passed":
        raise ProposalQueueError("apply requires passing verification evidence")
    verification = SkillVerificationEvidence(
        check=str(verification_payload.get("check") or ""),
        result="passed",
        evidence_ref=str(verification_payload.get("evidence_ref") or ""),
    )
    proposal = SkillProposal.model_validate(record["proposal"])
    return proposal, review, verification


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
    "record_proposal_verification",
)
