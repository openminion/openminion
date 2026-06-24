from __future__ import annotations

from typing import Any

import pytest

from openminion.modules.brain.runtime.recurrence import (
    RecurringTaskShape,
    TaskShapeRecurrenceWindow,
)
from openminion.modules.skill.proposal.promotion import (
    PromotionPassReport,
    _proposal_matches_existing_signatures,
    _proposal_signature_set,
    run_promotion_pass,
)
from openminion.modules.skill.proposal import (
    SkillProposal,
    SkillProposalDraft,
)
from openminion.modules.skill.proposal.review import (
    _RUNTIME_REVIEWER_IDS,
    decide_skill_proposal,
)


class _StubMemoryAPI:
    def __init__(
        self,
        *,
        shapes: list[RecurringTaskShape | dict[str, Any]] | None = None,
        catalog: list[dict[str, Any]] | None = None,
    ) -> None:
        self._shapes = list(shapes or [])
        self._catalog = list(catalog or [])
        self.recorded_proposals: list[SkillProposal] = []
        self.recorded_reviews: list[Any] = []

    def get_recurring_task_shapes(self) -> list[Any]:
        return list(self._shapes)

    def get_current_skill_catalog(self) -> list[Any]:
        return list(self._catalog)

    def record_promotion_proposal(self, proposal: SkillProposal) -> None:
        self.recorded_proposals.append(proposal)

    def record_promotion_review(self, review: Any) -> None:
        self.recorded_reviews.append(review)


def _shape(
    *,
    strategy_id: str = "research_strategy",
    capability_category: str = "live_information",
    intent_category: str = "latest_news",
    recurrence_count: int = 5,
    success_count: int | None = None,
    utility_score: float = 0.9,
) -> dict[str, Any]:

    payload: dict[str, Any] = {
        "task_shape_ref": (
            f"task_shape:{strategy_id}|{capability_category}|{intent_category}"
        ),
        "strategy_id": strategy_id,
        "capability_category": capability_category,
        "intent_category": intent_category,
        "recurrence_count": recurrence_count,
        "performance_entry_refs": [
            f"performance:{strategy_id}|{capability_category}|{intent_category}"
        ],
        "failure_pattern_refs": [],
        "knowledge_record_refs": [],
        "evidence_window": TaskShapeRecurrenceWindow().model_dump(mode="json"),
        "utility_score": utility_score,
    }
    if success_count is not None:
        payload["success_count"] = success_count
    return payload


def test_promotion_pass_dry_run_makes_no_mutations() -> None:
    memory = _StubMemoryAPI(
        shapes=[_shape(success_count=10, utility_score=0.95)],
        catalog=[],
    )

    report = run_promotion_pass(
        memory,
        success_threshold=3,
        utility_threshold=0.7,
        dry_run=True,
    )

    assert isinstance(report, PromotionPassReport)
    assert report.dry_run is True
    assert report.candidates_considered == 1
    assert report.proposals_drafted == 1
    assert report.pending_operator_review == 1
    assert report.auto_approved_structural_duplicates == 0
    # Dry-run: nothing recorded through the memory_api side.
    assert memory.recorded_proposals == []
    assert memory.recorded_reviews == []


def test_promotion_pass_skips_below_success_threshold() -> None:
    memory = _StubMemoryAPI(
        shapes=[
            _shape(success_count=1, utility_score=0.99),
            _shape(
                strategy_id="other_strategy",
                success_count=2,
                utility_score=0.99,
            ),
        ],
        catalog=[],
    )

    report = run_promotion_pass(
        memory,
        success_threshold=3,
        utility_threshold=0.5,
        dry_run=True,
    )

    assert report.candidates_considered == 2
    assert report.proposals_drafted == 0
    assert report.pending_operator_review == 0
    assert report.skipped_reasons.get("below_success_threshold") == 2


def test_promotion_pass_skips_below_utility_threshold() -> None:
    memory = _StubMemoryAPI(
        shapes=[
            _shape(success_count=10, utility_score=0.1),
            _shape(
                strategy_id="other_strategy",
                success_count=10,
                utility_score=0.2,
            ),
        ],
        catalog=[],
    )

    report = run_promotion_pass(
        memory,
        success_threshold=3,
        utility_threshold=0.5,
        dry_run=True,
    )

    assert report.candidates_considered == 2
    assert report.proposals_drafted == 0
    assert report.pending_operator_review == 0
    assert report.skipped_reasons.get("below_utility_threshold") == 2


def test_promotion_pass_holds_novel_proposals_for_operator_review() -> None:
    memory = _StubMemoryAPI(
        shapes=[_shape(success_count=10, utility_score=0.9)],
        catalog=[],
    )

    report = run_promotion_pass(
        memory,
        success_threshold=3,
        utility_threshold=0.7,
        dry_run=False,
    )

    assert report.pending_operator_review == 1
    assert report.auto_approved_structural_duplicates == 0
    # Novel proposals are recorded as pending, never applied as catalog
    # mutations. ``apply_emergent_results`` MUST remain empty.
    assert report.apply_emergent_results == []
    assert len(memory.recorded_proposals) == 1
    assert memory.recorded_reviews == []


def test_promotion_pass_auto_resolves_structural_duplicate_via_helper() -> None:

    catalog_row = {
        "skill_id": "existing.research_playbook",
        "name": "research-strategy-latest-news-playbook",
        "tags": ["research_strategy"],
        "applies_to": {"intents": ["latest_news"], "steps": []},
    }
    proposal = SkillProposal(
        proposal_id="proposal-overlap-1",
        source_task_shape_ref="task_shape:research_strategy|live_information|latest_news",
        proposed_skill_definition=SkillProposalDraft(
            name="research-strategy-latest-news-playbook",
            display_name="Research Strategy Latest News Playbook",
            short_description="overlap",
            tools=[],
            tags=["research_strategy"],
            applies_to={"intents": ["latest_news"], "steps": []},
            inputs_schema=[],
            verification_rules=[],
        ),
        evidence_refs=[],
        proposer_policy_id="skill_promotion_cadence_v1",
        proposed_at="",
    )

    # The proposal's signature set must intersect with the catalog's
    # signature set, and the helper must report the overlap.
    from openminion.modules.skill.proposal import _catalog_duplicate_signatures

    catalog_signatures = _catalog_duplicate_signatures([catalog_row])
    proposal_signatures = _proposal_signature_set(proposal)
    assert proposal_signatures & catalog_signatures, (
        "test scaffolding bug: proposal and catalog signatures must overlap"
    )
    assert _proposal_matches_existing_signatures(
        proposal, catalog_signatures=catalog_signatures
    )


def test_promotion_pass_runtime_reviewer_ids_remain_fail_closed() -> None:

    proposal_payload = SkillProposal(
        proposal_id="pinned-runtime-check",
        source_task_shape_ref="task_shape:any|any|any",
        proposed_skill_definition=SkillProposalDraft(
            name="any-playbook",
            display_name="Any Playbook",
            short_description="any",
            tools=[],
            tags=["any"],
            applies_to={"intents": ["any"], "steps": []},
            inputs_schema=[],
            verification_rules=[],
        ),
        evidence_refs=[],
        proposer_policy_id="skill_promotion_cadence_v1",
        proposed_at="",
    )
    # Sanity: the shipped frozenset is exactly the documented set.
    assert _RUNTIME_REVIEWER_IDS == frozenset(
        {"runtime", "system", "auto", "automatic", "self"}
    )
    for runtime_id in sorted(_RUNTIME_REVIEWER_IDS):
        with pytest.raises(ValueError):
            decide_skill_proposal(
                proposal_payload,
                reviewer_id=runtime_id,
                review_policy_id="any",
                criterion_decisions=[
                    {
                        "criterion_id": "any",
                        "status": "accepted",
                        "comment": "any",
                    }
                ],
            )


def test_promotion_pass_idempotent() -> None:

    shapes = [
        _shape(success_count=10, utility_score=0.9),
        _shape(
            strategy_id="other_strategy",
            success_count=5,
            utility_score=0.8,
        ),
    ]
    memory = _StubMemoryAPI(shapes=shapes, catalog=[])

    first = run_promotion_pass(
        memory,
        success_threshold=3,
        utility_threshold=0.5,
        dry_run=True,
    )
    second = run_promotion_pass(
        memory,
        success_threshold=3,
        utility_threshold=0.5,
        dry_run=True,
    )

    assert first.candidates_considered == second.candidates_considered
    assert first.proposals_drafted == second.proposals_drafted
    assert (
        first.auto_approved_structural_duplicates
        == second.auto_approved_structural_duplicates
    )
    assert first.pending_operator_review == second.pending_operator_review
    assert first.apply_emergent_results == [] == second.apply_emergent_results


def test_promotion_pass_handles_missing_memory_api_methods() -> None:

    class _Empty:
        pass

    report = run_promotion_pass(
        _Empty(),
        success_threshold=3,
        utility_threshold=0.5,
        dry_run=True,
    )

    assert report.candidates_considered == 0
    assert report.proposals_drafted == 0
    assert report.pending_operator_review == 0
    assert report.auto_approved_structural_duplicates == 0
    assert report.apply_emergent_results == []


def test_promotion_pass_accepts_typed_recurring_task_shape() -> None:

    typed_shape = RecurringTaskShape(
        task_shape_ref="task_shape:typed_strategy|cap_a|intent_b",
        strategy_id="typed_strategy",
        capability_category="cap_a",
        intent_category="intent_b",
        recurrence_count=7,
    )
    # No utility_score on the typed shape — defaults to 0.0; success
    # comes from recurrence_count.
    memory = _StubMemoryAPI(shapes=[typed_shape], catalog=[])

    report = run_promotion_pass(
        memory,
        success_threshold=3,
        utility_threshold=0.0,
        dry_run=True,
    )

    assert report.candidates_considered == 1
    assert report.proposals_drafted == 1
    assert report.pending_operator_review == 1
