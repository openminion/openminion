from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .base import (
    SkillProposal,
    _catalog_duplicate_signatures,
    propose_skills_from_task_shapes,
)
from .catalog import EmergentSkillCatalogAddition
from .review import SkillProposalReview, decide_skill_proposal

_STRUCTURAL_DEDUP_REVIEWER_ID = "structural_dedup"
_STRUCTURAL_DEDUP_POLICY_ID = "skill_promotion_cadence_v1"

_SKIPPED_BELOW_SUCCESS = "below_success_threshold"
_SKIPPED_BELOW_UTILITY = "below_utility_threshold"


@dataclass(frozen=True)
class PromotionPassReport:
    """Report from one ``run_promotion_pass`` invocation."""

    candidates_considered: int
    proposals_drafted: int
    auto_approved_structural_duplicates: int
    pending_operator_review: int
    apply_emergent_results: list[EmergentSkillCatalogAddition] = field(
        default_factory=list
    )
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    dry_run: bool = True


def _candidate_field(candidate: Any, field_name: str) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(field_name)
    return getattr(candidate, field_name, None)


def _candidate_success_count(candidate: Any) -> int:
    explicit = _candidate_field(candidate, "success_count")
    if explicit is not None:
        try:
            return int(explicit)
        except (TypeError, ValueError):
            return 0
    recurrence = _candidate_field(candidate, "recurrence_count")
    try:
        return int(recurrence or 0)
    except (TypeError, ValueError):
        return 0


def _candidate_utility(candidate: Any) -> float:
    for field_name in ("utility_score", "outcome_utility"):
        value = _candidate_field(candidate, field_name)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _best_effort_memory_call(memory_api: Any, method_name: str, *args: Any) -> Any:
    if memory_api is None:
        return None
    method = getattr(memory_api, method_name, None)
    if not callable(method):
        return None
    try:
        return method(*args)
    except Exception:
        return None


def _fetch_recurring_task_shapes(memory_api: Any) -> list[Any]:
    return list(_best_effort_memory_call(memory_api, "get_recurring_task_shapes") or [])


def _fetch_current_catalog(memory_api: Any) -> list[Any]:
    return list(_best_effort_memory_call(memory_api, "get_current_skill_catalog") or [])


def _record_pending_proposal(
    memory_api: Any, proposal: SkillProposal, *, dry_run: bool
) -> None:
    if dry_run:
        return
    _best_effort_memory_call(memory_api, "record_promotion_proposal", proposal)


def _record_structural_dismissal(
    memory_api: Any, review: SkillProposalReview, *, dry_run: bool
) -> None:
    if dry_run:
        return
    _best_effort_memory_call(memory_api, "record_promotion_review", review)


def _proposal_signature_set(proposal: SkillProposal) -> set[tuple[str, str, str]]:
    draft = proposal.proposed_skill_definition
    intents_raw: list[Any] = []
    if isinstance(draft.applies_to, Mapping):
        intents_raw = list(draft.applies_to.get("intents") or [])
    synthetic_row = {
        "skill_id": "",
        "name": str(draft.name or ""),
        "tags": list(draft.tags or []),
        "applies_to": {"intents": intents_raw, "steps": []},
    }
    return _catalog_duplicate_signatures([synthetic_row])


def _proposal_matches_existing_signatures(
    proposal: SkillProposal,
    *,
    catalog_signatures: set[tuple[str, str, str]],
) -> bool:
    if not catalog_signatures:
        return False
    proposal_signatures = _proposal_signature_set(proposal)
    return bool(proposal_signatures & catalog_signatures)


def run_promotion_pass(
    memory_api: Any,
    *,
    success_threshold: int,
    utility_threshold: float,
    dry_run: bool = True,
) -> PromotionPassReport:
    """Run one structural promotion pass without approving novel skills."""

    shapes = _fetch_recurring_task_shapes(memory_api)
    catalog = _fetch_current_catalog(memory_api)
    catalog_signatures = _catalog_duplicate_signatures(catalog)

    candidates_considered = 0
    skipped: dict[str, int] = {}
    qualifying: list[Any] = []
    for shape in shapes:
        candidates_considered += 1
        if _candidate_success_count(shape) < int(success_threshold):
            skipped[_SKIPPED_BELOW_SUCCESS] = skipped.get(_SKIPPED_BELOW_SUCCESS, 0) + 1
            continue
        if _candidate_utility(shape) < float(utility_threshold):
            skipped[_SKIPPED_BELOW_UTILITY] = skipped.get(_SKIPPED_BELOW_UTILITY, 0) + 1
            continue
        qualifying.append(shape)

    proposals = propose_skills_from_task_shapes(
        qualifying,
        current_catalog=catalog,
        policy_id=_STRUCTURAL_DEDUP_POLICY_ID,
    )

    auto_approved_structural_duplicates = 0
    pending_operator_review = 0
    for proposal in proposals:
        if _proposal_matches_existing_signatures(
            proposal, catalog_signatures=catalog_signatures
        ):
            review = decide_skill_proposal(
                proposal,
                reviewer_id=_STRUCTURAL_DEDUP_REVIEWER_ID,
                review_policy_id=_STRUCTURAL_DEDUP_POLICY_ID,
                criterion_decisions=[
                    {
                        "criterion_id": "structural_duplicate",
                        "status": "rejected",
                        "comment": (
                            "Proposal matches an existing catalog signature; "
                            "auto-dismissed by structural-dedup pass."
                        ),
                    }
                ],
            )
            _record_structural_dismissal(memory_api, review, dry_run=dry_run)
            auto_approved_structural_duplicates += 1
            continue
        _record_pending_proposal(memory_api, proposal, dry_run=dry_run)
        pending_operator_review += 1

    return PromotionPassReport(
        candidates_considered=candidates_considered,
        proposals_drafted=len(proposals),
        auto_approved_structural_duplicates=auto_approved_structural_duplicates,
        pending_operator_review=pending_operator_review,
        apply_emergent_results=[],
        skipped_reasons=skipped,
        dry_run=bool(dry_run),
    )


__all__ = (
    "PromotionPassReport",
    "run_promotion_pass",
)
