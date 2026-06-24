from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

from openminion.base.time import utc_now_iso
from .base import SkillProposalDraft
from openminion.modules.skill.constants import (
    SKILL_SOURCE_EMERGED_FROM_OBSERVATION,
    SKILL_SOURCE_OPERATOR_DECLARED,
    SKILL_STATUS_DRAFT,
)
from openminion.modules.skill.models import SkillPackage, normalize_text_list
from .review import SkillProposalReview

_RUNTIME_REVIEWER_IDS = frozenset({"runtime", "system", "auto", "automatic", "self"})


class SkillEmergenceReviewGateError(ValueError):
    """Raised when an emerged-skill write attempts to bypass review-gate."""


class EmergentSkillCatalogAddition(BaseModel):
    """Typed record of one emerged-skill catalog addition."""

    model_config = ConfigDict(extra="forbid")

    review_ref: str
    added_skill_id: str
    source_field: str
    added_at: str
    added_by: str


def _review_field(review: Any, field: str) -> Any:
    if isinstance(review, Mapping):
        return review.get(field)
    return getattr(review, field, None)


def _normalize_draft(
    skill_definition: SkillProposalDraft | Mapping[str, Any],
) -> SkillProposalDraft:
    return (
        skill_definition
        if isinstance(skill_definition, SkillProposalDraft)
        else SkillProposalDraft.model_validate(skill_definition)
    )


def _review_ref(review: SkillProposalReview | Mapping[str, Any]) -> str:
    for field in ("review_ref", "proposal_ref"):
        value = str(_review_field(review, field) or "").strip()
        if value:
            return value
    raise SkillEmergenceReviewGateError("review_ref is required")


def _reviewer_id(review: SkillProposalReview | Mapping[str, Any]) -> str:
    reviewer_id = str(_review_field(review, "reviewer_id") or "").strip()
    if not reviewer_id:
        raise SkillEmergenceReviewGateError("reviewer_id is required")
    if reviewer_id.lower() in _RUNTIME_REVIEWER_IDS:
        raise SkillEmergenceReviewGateError("reviewer_id must be operator-supplied")
    return reviewer_id


def _existing_catalog(
    catalog: Iterable[SkillPackage | Mapping[str, Any]],
) -> list[SkillPackage]:
    items: list[SkillPackage] = []
    for item in catalog or []:
        package = (
            item
            if isinstance(item, SkillPackage)
            else SkillPackage.from_dict(dict(item))
        )
        items.append(package)
    return items


def _validate_review_gate(
    review: SkillProposalReview | Mapping[str, Any],
) -> tuple[str, str]:
    review_ref = _review_ref(review)
    reviewer_id = _reviewer_id(review)
    status = str(_review_field(review, "status") or "").strip()
    if status != "accepted":
        raise SkillEmergenceReviewGateError("accepted review required")
    return review_ref, reviewer_id


def _emergent_skill_id(draft: SkillProposalDraft) -> str:
    name = str(draft.name or "").strip()
    if not name:
        raise SkillEmergenceReviewGateError("skill draft name is required")
    return f"emergent.{name}"


def _assert_additive_only(
    catalog: Iterable[SkillPackage], *, draft: SkillProposalDraft, skill_id: str
) -> None:
    draft_name = str(draft.name or "").strip()
    for existing in catalog:
        if existing.skill_id == skill_id:
            raise SkillEmergenceReviewGateError("emergent skill already exists")
        if (
            existing.source == SKILL_SOURCE_OPERATOR_DECLARED
            and str(existing.name or "").strip() == draft_name
        ):
            raise SkillEmergenceReviewGateError(
                "emergent skill conflicts with operator-declared catalog entry"
            )


def apply_emergent_skill(
    review: SkillProposalReview | Mapping[str, Any],
    *,
    catalog: Iterable[SkillPackage | Mapping[str, Any]],
    skill_definition: SkillProposalDraft | Mapping[str, Any],
) -> tuple[EmergentSkillCatalogAddition, list[SkillPackage]]:
    """Apply one accepted review to produce an additive emerged-skill entry."""

    draft = _normalize_draft(skill_definition)
    review_ref, reviewer_id = _validate_review_gate(review)
    existing_catalog = _existing_catalog(catalog)
    added_skill_id = _emergent_skill_id(draft)
    _assert_additive_only(existing_catalog, draft=draft, skill_id=added_skill_id)

    now = utc_now_iso()
    package = SkillPackage(
        skill_id=added_skill_id,
        name=str(draft.name or "").strip(),
        display_name=str(draft.display_name or "").strip() or None,
        short_description=str(draft.short_description or "").strip() or None,
        default_prompt=None,
        dependency_hints={},
        bundle_metadata={},
        status=SKILL_STATUS_DRAFT,
        version_hash="",
        source_artifact_ref=f"review:{review_ref}",
        tags=normalize_text_list(draft.tags),
        tools=normalize_text_list(draft.tools),
        reference_hints=[],
        risk_class=str(draft.risk_class or "").strip(),
        applies_to={
            "intents": normalize_text_list(draft.applies_to.get("intents")),
            "steps": normalize_text_list(draft.applies_to.get("steps")),
        },
        inputs_schema=list(draft.inputs_schema),
        snippets={},
        recipe=None,
        verification_rules=normalize_text_list(draft.verification_rules),
        rollback_hints=[],
        summary=str(draft.short_description or "").strip(),
        sections={"summary": str(draft.short_description or "").strip()},
        scope="global",
        agent_id=None,
        source_version=None,
        created_at=now,
        updated_at=now,
        source=SKILL_SOURCE_EMERGED_FROM_OBSERVATION,
    )
    package.version_hash = package.to_version_hash()

    addition = EmergentSkillCatalogAddition(
        review_ref=review_ref,
        added_skill_id=package.skill_id,
        source_field=SKILL_SOURCE_EMERGED_FROM_OBSERVATION,
        added_at=now,
        added_by=reviewer_id,
    )
    return addition, [*existing_catalog, package]


__all__ = (
    "EmergentSkillCatalogAddition",
    "SkillEmergenceReviewGateError",
    "apply_emergent_skill",
)
