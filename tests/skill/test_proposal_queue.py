from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.skill.proposal.catalog import (
    EmergentSkillCatalogAddition,
)
from openminion.modules.skill.proposal import (
    SkillProposal,
    SkillProposalDraft,
)
from openminion.modules.skill.proposal.queue import (
    PROPOSAL_QUEUE_STATE_APPLIED,
    PROPOSAL_QUEUE_STATE_PENDING,
    PROPOSAL_QUEUE_STATE_REVIEWED,
    ProposalQueueError,
    apply_proposal,
    create_proposal,
    get_proposal,
    list_proposals,
    record_proposal_review,
)
from openminion.modules.skill.proposal.review import _RUNTIME_REVIEWER_IDS
from openminion.modules.skill.storage import SQLiteSkillStore


def _store(tmp_path: Path) -> SQLiteSkillStore:
    return SQLiteSkillStore(tmp_path / "skill.db", wal=False)


def _proposal(*, proposal_id: str = "sprq-proposal-1") -> SkillProposal:
    return SkillProposal(
        proposal_id=proposal_id,
        source_task_shape_ref=(
            "task_shape:research_strategy|live_information|latest_news"
        ),
        proposed_skill_definition=SkillProposalDraft(
            name="research-latest-news-playbook",
            display_name="Research Latest News Playbook",
            short_description="From recurring research evidence.",
            tools=[],
            tags=["research_strategy", "live_information", "latest_news"],
            risk_class="low",
            applies_to={"intents": ["latest_news"], "steps": []},
            inputs_schema=[],
            verification_rules=[],
        ),
        evidence_refs=["performance:research_strategy|live_information|latest_news"],
        proposer_policy_id="skill_promotion_cadence_v1",
        proposed_at="",
    )


def test_create_proposal_persists_pending_record(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        record = create_proposal(store, _proposal())
        assert record["proposal_id"] == "sprq-proposal-1"
        assert record["queue_state"] == PROPOSAL_QUEUE_STATE_PENDING
        assert record["created_now"] is True
        assert record["review"] is None
        assert record["applied_addition"] is None
        assert record["proposed_at"]
    finally:
        store.close()


def test_create_proposal_is_idempotent_on_proposal_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first = create_proposal(store, _proposal())
        second = create_proposal(store, _proposal())
        # Same proposal id => no overwrite, no second row.
        assert first["proposal_id"] == second["proposal_id"]
        assert first["created_now"] is True
        assert second["created_now"] is False
        listed = list_proposals(store)
        assert len(listed) == 1
    finally:
        store.close()


def test_list_proposals_filters_by_queue_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal(proposal_id="a"))
        create_proposal(store, _proposal(proposal_id="b"))
        all_proposals = list_proposals(store)
        assert {row["proposal_id"] for row in all_proposals} == {"a", "b"}
        pending = list_proposals(store, queue_state=PROPOSAL_QUEUE_STATE_PENDING)
        assert {row["proposal_id"] for row in pending} == {"a", "b"}
        reviewed = list_proposals(store, queue_state=PROPOSAL_QUEUE_STATE_REVIEWED)
        assert reviewed == []
    finally:
        store.close()


def test_list_proposals_rejects_unknown_queue_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ProposalQueueError):
            list_proposals(store, queue_state="not-a-real-state")
    finally:
        store.close()


def test_record_proposal_review_persists_and_transitions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        review = record_proposal_review(
            store,
            proposal_id="sprq-proposal-1",
            reviewer_id="operator-42",
            review_policy_id="sprq_review_policy_v1",
            criterion_decisions=[
                {
                    "criterion_id": "fit",
                    "status": "accepted",
                    "comment": "Matches recurring intent.",
                },
            ],
        )
        assert review.status == "accepted"
        assert review.reviewer_id == "operator-42"
        record = get_proposal(store, proposal_id="sprq-proposal-1")
        assert record is not None
        assert record["queue_state"] == PROPOSAL_QUEUE_STATE_REVIEWED
        assert record["reviewer_id"] == "operator-42"
        assert record["review_status"] == "accepted"
    finally:
        store.close()


@pytest.mark.parametrize("runtime_id", sorted(_RUNTIME_REVIEWER_IDS))
def test_record_proposal_review_fails_closed_on_runtime_reviewer(
    tmp_path: Path, runtime_id: str
) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        # The shipped decide_skill_proposal() raises ValueError BEFORE
        # any persistence. The store must remain in `pending` state.
        with pytest.raises(ValueError):
            record_proposal_review(
                store,
                proposal_id="sprq-proposal-1",
                reviewer_id=runtime_id,
                review_policy_id="sprq_review_policy_v1",
                criterion_decisions=[
                    {
                        "criterion_id": "fit",
                        "status": "accepted",
                        "comment": "should never persist",
                    },
                ],
            )
        record = get_proposal(store, proposal_id="sprq-proposal-1")
        assert record is not None
        assert record["queue_state"] == PROPOSAL_QUEUE_STATE_PENDING
        assert record["review"] is None
        assert record["reviewer_id"] == ""
    finally:
        store.close()


def test_record_proposal_review_rejects_unknown_proposal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(ProposalQueueError):
            record_proposal_review(
                store,
                proposal_id="missing-id",
                reviewer_id="operator-9",
                review_policy_id="any",
                criterion_decisions=[
                    {
                        "criterion_id": "fit",
                        "status": "accepted",
                        "comment": "any",
                    },
                ],
            )
    finally:
        store.close()


def test_apply_proposal_flows_through_apply_emergent_skill(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        record_proposal_review(
            store,
            proposal_id="sprq-proposal-1",
            reviewer_id="operator-42",
            review_policy_id="sprq_review_policy_v1",
            criterion_decisions=[
                {
                    "criterion_id": "fit",
                    "status": "accepted",
                    "comment": "Accept.",
                },
            ],
        )
        addition = apply_proposal(
            store,
            proposal_id="sprq-proposal-1",
            current_catalog=[],
        )
        assert isinstance(addition, EmergentSkillCatalogAddition)
        assert addition.added_skill_id.startswith("emergent.")
        assert addition.added_by == "operator-42"
        record = get_proposal(store, proposal_id="sprq-proposal-1")
        assert record is not None
        assert record["queue_state"] == PROPOSAL_QUEUE_STATE_APPLIED
        assert record["applied_addition"] is not None
    finally:
        store.close()


def test_apply_proposal_is_idempotent_after_first_apply(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        record_proposal_review(
            store,
            proposal_id="sprq-proposal-1",
            reviewer_id="operator-42",
            review_policy_id="sprq_review_policy_v1",
            criterion_decisions=[
                {
                    "criterion_id": "fit",
                    "status": "accepted",
                    "comment": "Accept.",
                },
            ],
        )
        first = apply_proposal(store, proposal_id="sprq-proposal-1", current_catalog=[])
        second = apply_proposal(
            store, proposal_id="sprq-proposal-1", current_catalog=[]
        )
        assert first.added_skill_id == second.added_skill_id
        assert first.review_ref == second.review_ref
    finally:
        store.close()


def test_apply_proposal_refuses_pending_proposal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        with pytest.raises(ProposalQueueError):
            apply_proposal(
                store,
                proposal_id="sprq-proposal-1",
                current_catalog=[],
            )
    finally:
        store.close()


def test_apply_proposal_refuses_non_accepted_review(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        record_proposal_review(
            store,
            proposal_id="sprq-proposal-1",
            reviewer_id="operator-42",
            review_policy_id="sprq_review_policy_v1",
            criterion_decisions=[
                {
                    "criterion_id": "policy",
                    "status": "rejected",
                    "comment": "Out of policy.",
                },
            ],
        )
        with pytest.raises(ProposalQueueError):
            apply_proposal(
                store,
                proposal_id="sprq-proposal-1",
                current_catalog=[],
            )
    finally:
        store.close()


def test_get_proposal_returns_none_for_missing_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        assert get_proposal(store, proposal_id="not-there") is None
    finally:
        store.close()


def test_record_review_blocked_after_apply(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        record_proposal_review(
            store,
            proposal_id="sprq-proposal-1",
            reviewer_id="operator-42",
            review_policy_id="sprq_review_policy_v1",
            criterion_decisions=[
                {
                    "criterion_id": "fit",
                    "status": "accepted",
                    "comment": "Accept.",
                },
            ],
        )
        apply_proposal(store, proposal_id="sprq-proposal-1", current_catalog=[])
        with pytest.raises(ValueError):
            record_proposal_review(
                store,
                proposal_id="sprq-proposal-1",
                reviewer_id="operator-42",
                review_policy_id="sprq_review_policy_v1",
                criterion_decisions=[
                    {
                        "criterion_id": "fit",
                        "status": "rejected",
                        "comment": "Change of heart.",
                    },
                ],
            )
    finally:
        store.close()
