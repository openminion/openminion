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
    record_proposal_verification,
)
from openminion.modules.skill.interfaces import (
    SkillIngestAuthority,
    SkillVerificationEvidence,
)
from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.proposal.review import _RUNTIME_REVIEWER_IDS
from openminion.modules.skill.storage import SQLiteSkillStore
from openminion.modules.skill.runtime.skill import Skill


def _store(tmp_path: Path) -> SQLiteSkillStore:
    return SQLiteSkillStore(tmp_path / "skill.db", wal=False)


def _skill(tmp_path: Path) -> Skill:
    return Skill(
        {
            "skill": {
                "sqlite_path": str(tmp_path / "skill.db"),
                "blob_root": str(tmp_path / "blob"),
                "fallback_root": str(tmp_path / "fallback"),
                "wal": False,
            }
        }
    )


def _authority(reviewer_id: str = "local:test") -> SkillIngestAuthority:
    return SkillIngestAuthority.local_operator(
        surface="test.skill.proposal_apply",
        principal_id=reviewer_id,
    )


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
        skill_markdown="""---
name: research-latest-news-playbook
description: Research current news from reviewed sources.
verification:
  - Confirm cited sources are current.
---
# Research Latest News Playbook

## Procedure

Research the requested topic and cite current sources.
""",
        evidence_refs=["performance:research_strategy|live_information|latest_news"],
        proposer_policy_id="skill_promotion_cadence_v1",
        proposed_at="",
    )


def _review_and_verify(
    store: SQLiteSkillStore,
    *,
    proposal_id: str = "sprq-proposal-1",
    reviewer_id: str = "local:test",
) -> None:
    record_proposal_review(
        store,
        proposal_id=proposal_id,
        reviewer_id=reviewer_id,
        review_policy_id="sprq_review_policy_v1",
        criterion_decisions=[
            {"criterion_id": "fit", "status": "accepted", "comment": "Accept."}
        ],
    )
    record_proposal_verification(
        store,
        proposal_id=proposal_id,
        operator_id=reviewer_id,
        evidence=SkillVerificationEvidence(
            check="pytest",
            result="passed",
            evidence_ref="artifact://validation/pytest.txt",
        ),
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


def test_record_proposal_verification_survives_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "skill.db"
    store = SQLiteSkillStore(db_path, wal=False)
    create_proposal(store, _proposal())
    record_proposal_review(
        store,
        proposal_id="sprq-proposal-1",
        reviewer_id="local:test",
        review_policy_id="review-v1",
        criterion_decisions=[
            {"criterion_id": "fit", "status": "accepted", "comment": "ok"}
        ],
    )
    record_proposal_verification(
        store,
        proposal_id="sprq-proposal-1",
        operator_id="local:test",
        evidence=SkillVerificationEvidence(
            check="pytest",
            result="passed",
            evidence_ref="artifact://validation/pytest.txt",
        ),
    )
    store.close()

    reopened = SQLiteSkillStore(db_path, wal=False)
    try:
        record = get_proposal(reopened, proposal_id="sprq-proposal-1")
        assert record is not None
        assert record["verification_evidence"] == {
            "check": "pytest",
            "result": "passed",
            "evidence_ref": "artifact://validation/pytest.txt",
            "reviewer_id": "local:test",
        }
    finally:
        reopened.close()


def test_record_proposal_verification_requires_matching_reviewer(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        create_proposal(store, _proposal())
        record_proposal_review(
            store,
            proposal_id="sprq-proposal-1",
            reviewer_id="local:test",
            review_policy_id="review-v1",
            criterion_decisions=[
                {"criterion_id": "fit", "status": "accepted", "comment": "ok"}
            ],
        )
        with pytest.raises(ProposalQueueError):
            record_proposal_verification(
                store,
                proposal_id="sprq-proposal-1",
                operator_id="local:other",
                evidence=SkillVerificationEvidence(
                    check="pytest",
                    result="passed",
                    evidence_ref="artifact://validation/pytest.txt",
                ),
            )
    finally:
        store.close()


def test_apply_proposal_admits_exact_markdown(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    try:
        create_proposal(skill.store, _proposal())
        _review_and_verify(skill.store)
        addition = apply_proposal(
            skill,
            proposal_id="sprq-proposal-1",
            authority=_authority(),
        )
        assert isinstance(addition, EmergentSkillCatalogAddition)
        assert addition.added_skill_id == "research_latest_news_playbook"
        assert addition.added_by == "local:test"
        assert skill.get_skill(addition.added_skill_id).version_hash == (
            addition.version_hash
        )
        record = get_proposal(skill.store, proposal_id="sprq-proposal-1")
        assert record is not None
        assert record["queue_state"] == PROPOSAL_QUEUE_STATE_APPLIED
        assert record["applied_addition"] is not None
    finally:
        skill.close()


def test_apply_proposal_is_idempotent_after_first_apply(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    try:
        create_proposal(skill.store, _proposal())
        _review_and_verify(skill.store)
        first = apply_proposal(
            skill, proposal_id="sprq-proposal-1", authority=_authority()
        )
        second = apply_proposal(
            skill, proposal_id="sprq-proposal-1", authority=_authority()
        )
        assert first.added_skill_id == second.added_skill_id
        assert first.version_hash == second.version_hash
    finally:
        skill.close()


def test_apply_proposal_refuses_pending_proposal(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    try:
        create_proposal(skill.store, _proposal())
        with pytest.raises(ProposalQueueError):
            apply_proposal(
                skill,
                proposal_id="sprq-proposal-1",
                authority=_authority(),
            )
    finally:
        skill.close()


def test_apply_proposal_refuses_non_accepted_review(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    try:
        create_proposal(skill.store, _proposal())
        record_proposal_review(
            skill.store,
            proposal_id="sprq-proposal-1",
            reviewer_id="local:test",
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
                skill,
                proposal_id="sprq-proposal-1",
                authority=_authority(),
            )
    finally:
        skill.close()


def test_apply_proposal_requires_verification(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    try:
        create_proposal(skill.store, _proposal())
        record_proposal_review(
            skill.store,
            proposal_id="sprq-proposal-1",
            reviewer_id="local:test",
            review_policy_id="sprq_review_policy_v1",
            criterion_decisions=[
                {"criterion_id": "fit", "status": "accepted", "comment": "Accept."}
            ],
        )
        with pytest.raises(ProposalQueueError, match="verification"):
            apply_proposal(
                skill,
                proposal_id="sprq-proposal-1",
                authority=_authority(),
            )
    finally:
        skill.close()


def test_apply_proposal_requires_same_local_operator(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    try:
        create_proposal(skill.store, _proposal())
        _review_and_verify(skill.store)
        with pytest.raises(ProposalQueueError, match="accepted reviewer"):
            apply_proposal(
                skill,
                proposal_id="sprq-proposal-1",
                authority=_authority("local:other"),
            )
    finally:
        skill.close()


def test_apply_proposal_rejects_failed_stored_verification(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    try:
        create_proposal(skill.store, _proposal())
        record_proposal_review(
            skill.store,
            proposal_id="sprq-proposal-1",
            reviewer_id="local:test",
            review_policy_id="sprq_review_policy_v1",
            criterion_decisions=[
                {"criterion_id": "fit", "status": "accepted", "comment": "Accept."}
            ],
        )
        skill.store.record_proposal_verification(
            proposal_id="sprq-proposal-1",
            verification_evidence_json=(
                '{"check":"pytest","result":"failed",'
                '"evidence_ref":"artifact://validation/failed.txt",'
                '"reviewer_id":"local:test"}'
            ),
            updated_at="2026-09-05T00:00:00+00:00",
        )

        with pytest.raises(ProposalQueueError, match="passing verification"):
            apply_proposal(
                skill,
                proposal_id="sprq-proposal-1",
                authority=_authority(),
            )
    finally:
        skill.close()


def test_apply_proposal_rejects_active_identity_collision(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    authority = _authority()
    try:
        existing_id, existing_hash, _warnings = skill.ingest_text(
            name="research-latest-news-playbook",
            markdown=_proposal().skill_markdown.replace(
                "Research the requested topic and cite current sources.",
                "Keep the existing catalog procedure.",
            ),
            authority=authority,
        )
        skill.admit_skill_version(
            skill_id=existing_id,
            version_hash=existing_hash,
            expected_active_version_hash=None,
            target_status="verified",
            reason="test setup",
            authority=authority,
            verification_evidence=SkillVerificationEvidence(
                check="pytest",
                result="passed",
                evidence_ref="artifact://validation/setup.txt",
            ),
        )
        create_proposal(skill.store, _proposal())
        _review_and_verify(skill.store)

        with pytest.raises(ProposalQueueError, match="active skill identity"):
            apply_proposal(
                skill,
                proposal_id="sprq-proposal-1",
                authority=authority,
            )

        assert skill.get_skill(existing_id).version_hash == existing_hash
        assert get_proposal(
            skill.store, proposal_id="sprq-proposal-1"
        )["queue_state"] == PROPOSAL_QUEUE_STATE_REVIEWED
    finally:
        skill.close()


def test_apply_proposal_retries_after_admission_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = _skill(tmp_path)
    try:
        create_proposal(skill.store, _proposal())
        _review_and_verify(skill.store)
        original_admit = skill.admit_skill_version
        attempts = 0

        def fail_once(**kwargs: object) -> dict[str, object]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise SkillError("TEST_ADMISSION_FAILURE", "test interruption")
            return original_admit(**kwargs)

        monkeypatch.setattr(skill, "admit_skill_version", fail_once)

        with pytest.raises(SkillError, match="test interruption"):
            apply_proposal(
                skill,
                proposal_id="sprq-proposal-1",
                authority=_authority(),
            )
        assert get_proposal(
            skill.store, proposal_id="sprq-proposal-1"
        )["queue_state"] == PROPOSAL_QUEUE_STATE_REVIEWED

        addition = apply_proposal(
            skill,
            proposal_id="sprq-proposal-1",
            authority=_authority(),
        )
        assert addition.added_skill_id == "research_latest_news_playbook"
        assert attempts == 2
        retry_id, retry_hash, _warnings = skill.ingest_text(
            name="research-latest-news-playbook",
            markdown=_proposal().skill_markdown,
            authority=_authority(),
        )
        assert (retry_id, retry_hash) == (
            addition.added_skill_id,
            addition.version_hash,
        )
    finally:
        skill.close()


def test_get_proposal_returns_none_for_missing_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        assert get_proposal(store, proposal_id="not-there") is None
    finally:
        store.close()


def test_record_review_blocked_after_apply(tmp_path: Path) -> None:
    skill = _skill(tmp_path)
    try:
        create_proposal(skill.store, _proposal())
        _review_and_verify(skill.store)
        apply_proposal(skill, proposal_id="sprq-proposal-1", authority=_authority())
        with pytest.raises(ValueError):
            record_proposal_review(
                skill.store,
                proposal_id="sprq-proposal-1",
                reviewer_id="local:test",
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
        skill.close()
