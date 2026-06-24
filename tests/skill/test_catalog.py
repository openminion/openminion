from __future__ import annotations

import pytest

from openminion.modules.brain.runtime.proposal import SkillProposalDraft
from openminion.modules.skill.proposal.catalog import (
    EmergentSkillCatalogAddition,
    SkillEmergenceReviewGateError,
    apply_emergent_skill,
)
from openminion.modules.skill.constants import (
    SKILL_SOURCE_EMERGED_FROM_OBSERVATION,
    SKILL_SOURCE_OPERATOR_DECLARED,
    SKILL_STATUS_DRAFT,
)
from openminion.modules.skill.models import SkillPackage
from openminion.modules.skill.proposal.review import SkillProposalReview


def _draft() -> SkillProposalDraft:
    return SkillProposalDraft(
        name="research_latest_news_playbook",
        display_name="Research Latest News Playbook",
        short_description="Emergent skill from recurring evidence.",
        tools=["web.search"],
        tags=["research", "live_information", "latest_news"],
        risk_class="low",
        applies_to={"intents": ["latest_news"], "steps": []},
        inputs_schema=[],
        verification_rules=["confirm sources"],
    )


def _review(
    *,
    proposal_ref: str = "proposal-7",
    status: str = "accepted",
    reviewer_id: str = "operator-9",
) -> SkillProposalReview:
    return SkillProposalReview(
        proposal_ref=proposal_ref,
        status=status,
        reviewer_id=reviewer_id,
        review_policy_id="sprv_policy_v1",
        decided_at="2026-05-13T23:00:00+00:00",
        reviewer_notes=[],
    )


def _existing_operator_skill(*, name: str = "existing_skill") -> SkillPackage:
    return SkillPackage(
        skill_id=f"skill.{name}",
        name=name,
        display_name=name.replace("_", " ").title(),
        short_description="Operator skill.",
        default_prompt=None,
        dependency_hints={},
        bundle_metadata={},
        status="verified",
        version_hash="v1",
        source_artifact_ref="artifact://skill",
        tags=[],
        tools=[],
        reference_hints=[],
        risk_class="low",
        applies_to={"intents": [], "steps": []},
        inputs_schema=[],
        snippets={},
        recipe=None,
        verification_rules=[],
        rollback_hints=[],
        summary="Operator skill.",
        sections={},
        scope="global",
        agent_id=None,
        source_version=None,
        created_at="2026-05-11T00:00:00+00:00",
        updated_at="2026-05-11T00:00:00+00:00",
    )


def test_apply_emergent_skill_adds_typed_catalog_entry() -> None:
    addition, catalog = apply_emergent_skill(
        _review(),
        catalog=[],
        skill_definition=_draft(),
    )
    assert isinstance(addition, EmergentSkillCatalogAddition)
    assert addition.review_ref == "proposal-7"
    assert addition.added_skill_id == "emergent.research_latest_news_playbook"
    assert addition.source_field == SKILL_SOURCE_EMERGED_FROM_OBSERVATION
    assert addition.added_by == "operator-9"
    assert len(catalog) == 1
    package = catalog[0]
    assert package.skill_id == "emergent.research_latest_news_playbook"
    assert package.status == SKILL_STATUS_DRAFT
    assert package.source == SKILL_SOURCE_EMERGED_FROM_OBSERVATION
    assert package.version_hash


@pytest.mark.parametrize("status", ["rejected", "deferred"])
def test_apply_emergent_skill_rejects_non_accepted_reviews(status: str) -> None:
    with pytest.raises(SkillEmergenceReviewGateError):
        apply_emergent_skill(
            _review(status=status),
            catalog=[],
            skill_definition=_draft(),
        )


@pytest.mark.parametrize(
    "reviewer_id", ["", "   ", "runtime", "system", "auto", "self"]
)
def test_apply_emergent_skill_rejects_blank_or_runtime_reviewers(
    reviewer_id: str,
) -> None:
    with pytest.raises(SkillEmergenceReviewGateError):
        apply_emergent_skill(
            _review(reviewer_id=reviewer_id),
            catalog=[],
            skill_definition=_draft(),
        )


def test_apply_emergent_skill_rejects_missing_review_ref() -> None:
    with pytest.raises(SkillEmergenceReviewGateError):
        apply_emergent_skill(
            {
                "status": "accepted",
                "reviewer_id": "operator-9",
                "review_policy_id": "sprv_policy_v1",
                "decided_at": "2026-05-13T23:00:00+00:00",
                "reviewer_notes": [],
            },
            catalog=[],
            skill_definition=_draft(),
        )


def test_apply_emergent_skill_is_additive_only_against_operator_declared_catalog() -> (
    None
):
    with pytest.raises(SkillEmergenceReviewGateError):
        apply_emergent_skill(
            _review(),
            catalog=[_existing_operator_skill(name="research_latest_news_playbook")],
            skill_definition=_draft(),
        )


def test_skill_package_from_dict_defaults_source_for_backward_compat() -> None:
    package = SkillPackage.from_dict(
        {
            "skill_id": "skill.legacy",
            "name": "legacy",
            "status": "draft",
            "version_hash": "v1",
            "source_artifact_ref": "artifact://legacy",
            "tags": [],
            "tools": [],
            "reference_hints": [],
            "risk_class": "low",
            "applies_to": {"intents": [], "steps": []},
            "inputs_schema": [],
            "snippets": {},
            "verification_rules": [],
            "rollback_hints": [],
            "summary": "",
            "sections": {},
            "scope": "global",
            "created_at": "2026-05-11T00:00:00+00:00",
            "updated_at": "2026-05-11T00:00:00+00:00",
        }
    )
    assert package.source == SKILL_SOURCE_OPERATOR_DECLARED


def test_seca_schema_fields_do_not_expose_bulk_or_auto_addition_surface() -> None:
    schema_fields = set(EmergentSkillCatalogAddition.model_fields.keys()) | set(
        SkillPackage.__dataclass_fields__.keys()
    )
    forbidden = ("bulk", "auto", "trigger", "reasoning", "narrative", "guess")
    for field_name in schema_fields:
        for fragment in forbidden:
            assert fragment not in field_name
