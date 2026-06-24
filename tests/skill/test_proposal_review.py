from __future__ import annotations

import pytest

from openminion.modules.brain.runtime.proposal import (
    SkillProposal,
    SkillProposalDraft,
)
from openminion.modules.skill.proposal.review import (
    SkillProposalCriterionDecision,
    SkillProposalReview,
    decide_skill_proposal,
    list_pending_proposals,
)


def _proposal(*, proposal_id: str = "proposal-2") -> SkillProposal:
    return SkillProposal(
        proposal_id=proposal_id,
        source_task_shape_ref="task_shape:research|live_information|latest_news",
        proposed_skill_definition=SkillProposalDraft(
            name="research_latest_news_playbook",
            display_name="Research Latest News Playbook",
            short_description="Proposed skill.",
            tools=[],
            tags=["research", "live_information", "latest_news"],
            applies_to={"intents": ["latest_news"], "steps": []},
            inputs_schema=[],
            verification_rules=[],
        ),
        evidence_refs=["performance:research|live_information|latest_news"],
        proposer_policy_id="skill_proposal_v1",
        proposed_at="",
    )


def test_decide_skill_proposal_emits_typed_review() -> None:
    review = decide_skill_proposal(
        _proposal(),
        reviewer_id="operator-7",
        review_policy_id="sprv_policy_v1",
        criterion_decisions=[
            {
                "criterion_id": "safety",
                "status": "accepted",
                "comment": "Safe to stage for human review.",
            }
        ],
    )
    assert isinstance(review, SkillProposalReview)
    assert review.proposal_ref == "proposal-2"
    assert review.status == "accepted"
    assert review.reviewer_id == "operator-7"
    assert review.review_policy_id == "sprv_policy_v1"
    assert review.decided_at
    assert review.reviewer_notes == [
        SkillProposalCriterionDecision(
            criterion_id="safety",
            status="accepted",
            comment="Safe to stage for human review.",
        )
    ]


def test_decide_skill_proposal_rolls_up_rejected_before_deferred() -> None:
    review = decide_skill_proposal(
        _proposal(),
        reviewer_id="operator-7",
        review_policy_id="sprv_policy_v1",
        criterion_decisions=[
            {"criterion_id": "fit", "status": "accepted", "comment": "Matches intent."},
            {
                "criterion_id": "risk",
                "status": "deferred",
                "comment": "Need another pass.",
            },
            {
                "criterion_id": "policy",
                "status": "rejected",
                "comment": "Blocked by policy.",
            },
        ],
    )
    assert review.status == "rejected"


@pytest.mark.parametrize(
    "reviewer_id", ["", "   ", "runtime", "system", "auto", "self"]
)
def test_decide_skill_proposal_rejects_runtime_or_blank_reviewer_ids(
    reviewer_id: str,
) -> None:
    with pytest.raises(ValueError):
        decide_skill_proposal(
            _proposal(),
            reviewer_id=reviewer_id,
            review_policy_id="sprv_policy_v1",
            criterion_decisions=[
                {"criterion_id": "fit", "status": "accepted", "comment": "Looks good."}
            ],
        )


def test_decide_skill_proposal_requires_structured_criterion_comments() -> None:
    with pytest.raises(ValueError):
        decide_skill_proposal(
            _proposal(),
            reviewer_id="operator-7",
            review_policy_id="sprv_policy_v1",
            criterion_decisions=[
                {"criterion_id": "fit", "status": "accepted", "comment": ""}
            ],
        )


def test_list_pending_proposals_returns_sorted_typed_proposals() -> None:
    class PendingBackend:
        def list_pending_skill_proposals(
            self, *, limit: int
        ) -> list[dict[str, object]]:
            assert limit == 3
            return [
                _proposal(proposal_id="proposal-9").model_dump(mode="json"),
                {"proposal_id": "", "source_task_shape_ref": "x"},
                _proposal(proposal_id="proposal-1").model_dump(mode="json"),
            ]

    proposals = list_pending_proposals(PendingBackend(), limit=3)
    assert [proposal.proposal_id for proposal in proposals] == [
        "proposal-1",
        "proposal-9",
    ]


def test_list_pending_proposals_uses_backend_or_store_and_handles_errors() -> None:
    class PendingStore:
        def list_pending_proposals(self, *, limit: int) -> list[SkillProposal]:
            raise RuntimeError("nope")

    class PendingAPI:
        store = PendingStore()

    assert list_pending_proposals(PendingAPI(), limit=2) == []


def test_review_schema_fields_do_not_expose_freeform_or_catalog_mutation_fields() -> (
    None
):
    schema_fields = set(SkillProposalReview.model_fields.keys()) | set(
        SkillProposalCriterionDecision.model_fields.keys()
    )
    forbidden = ("summary", "reasoning", "narrative", "catalog", "bulk")
    for field_name in schema_fields:
        for fragment in forbidden:
            assert fragment not in field_name
    assert "reviewer_notes" in schema_fields
    assert "comment" in schema_fields
