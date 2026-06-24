from __future__ import annotations

from types import SimpleNamespace

from openminion.modules.brain.runtime.recurrence import RecurringTaskShape
from openminion.modules.brain.runtime.proposal import (
    SkillProposal,
    SkillProposalDraft,
    propose_skills_from_task_shapes,
)


def _shape(
    *,
    ref: str = "task_shape:research|live_information|latest_news",
    strategy_id: str = "research",
    capability_category: str = "live_information",
    intent_category: str = "latest_news",
) -> RecurringTaskShape:
    return RecurringTaskShape(
        task_shape_ref=ref,
        strategy_id=strategy_id,
        capability_category=capability_category,
        intent_category=intent_category,
        recurrence_count=3,
        performance_entry_refs=["performance:research|live_information|latest_news"],
        failure_pattern_refs=["failure:strategy_outcome|strategy_outcome_failure"],
        knowledge_record_refs=["knowledge:k-1"],
        evidence_window={"min_recurrence_threshold": 2},
    )


def test_proposal_emits_typed_draft_from_shape() -> None:
    proposals = propose_skills_from_task_shapes(
        [_shape()],
        current_catalog=[],
        policy_id="skill_proposal_v1",
    )
    assert len(proposals) == 1
    proposal = proposals[0]
    assert (
        proposal.source_task_shape_ref
        == "task_shape:research|live_information|latest_news"
    )
    assert proposal.proposer_policy_id == "skill_proposal_v1"
    assert proposal.proposed_at == ""
    assert (
        proposal.proposed_skill_definition.display_name
        == "Research Latest News Playbook"
    )
    assert proposal.proposed_skill_definition.tags == [
        "research",
        "live_information",
        "latest_news",
    ]
    assert proposal.proposed_skill_definition.applies_to == {
        "intents": ["latest_news"],
        "steps": [],
    }


def test_proposal_duplicate_avoidance_against_existing_catalog_signature() -> None:
    catalog_item = SimpleNamespace(
        skill_id="research_skill",
        name="Research Skill",
        tags=["live_information"],
        applies_to={"intents": ["latest_news"]},
    )
    proposals = propose_skills_from_task_shapes(
        [_shape()],
        current_catalog=[catalog_item],
        policy_id="skill_proposal_v1",
    )
    assert proposals == []


def test_proposal_skips_shape_without_source_task_shape_ref() -> None:
    shapes = [
        _shape(ref=""),
        _shape(ref="   "),
    ]
    proposals = propose_skills_from_task_shapes(
        shapes,
        current_catalog=[],
        policy_id="skill_proposal_v1",
    )
    assert proposals == []


def test_proposal_is_deterministic() -> None:
    shapes = [_shape()]
    a = propose_skills_from_task_shapes(
        shapes,
        current_catalog=[],
        policy_id="skill_proposal_v1",
    )
    b = propose_skills_from_task_shapes(
        shapes,
        current_catalog=[],
        policy_id="skill_proposal_v1",
    )
    assert [item.model_dump(mode="json") for item in a] == [
        item.model_dump(mode="json") for item in b
    ]


def test_proposal_has_no_side_effect_on_catalog_container() -> None:
    catalog = [
        {
            "skill_id": "existing",
            "name": "Existing",
            "tags": ["other"],
            "applies_to": {"intents": ["different"]},
        }
    ]
    before = list(catalog)
    _ = propose_skills_from_task_shapes(
        [_shape()],
        current_catalog=catalog,
        policy_id="skill_proposal_v1",
    )
    assert catalog == before


def test_proposal_schemas_do_not_expose_status_or_catalog_mutation_fields() -> None:
    schema_fields = set(SkillProposal.model_fields.keys()) | set(
        SkillProposalDraft.model_fields.keys()
    )
    assert "source_task_shape_ref" in schema_fields
    assert "source" not in schema_fields
    forbidden = ("status", "accepted", "review", "catalog", "summary")
    for field_name in schema_fields:
        for fragment in forbidden:
            assert fragment not in field_name
