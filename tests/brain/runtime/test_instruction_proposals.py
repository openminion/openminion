from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.modules.brain.runtime.improvement.instructions import (
    InstructionApprovalRecord,
    InstructionOpportunity,
    InstructionProposal,
    build_instruction_proposal,
    compute_instruction_proposal_hash,
)


def test_instruction_opportunity_is_structural_only() -> None:
    opportunity = InstructionOpportunity(
        opportunity_id="opp-1",
        source_kind="self_improvement_note",
        evidence_refs=["note:one"],
        observed_count=2,
    )

    assert opportunity.needs_authoring is True
    assert opportunity.evidence_refs == ["note:one"]


def test_instruction_proposal_hash_round_trips() -> None:
    proposal = build_instruction_proposal(
        candidate_id="cand-1",
        target_file="/tmp/project/OPENMINION.md",
        target_name="OPENMINION.md",
        proposal_kind="append_bullet",
        summary="Add validation note",
        evidence_refs=["trace:1"],
        author_source="operator",
        suggested_text="Run focused tests before closeout.",
        target_content_hash="abc",
    )

    assert proposal.proposal_hash == compute_instruction_proposal_hash(
        candidate_id="cand-1",
        target_file="/tmp/project/OPENMINION.md",
        target_name="OPENMINION.md",
        proposal_kind="append_bullet",
        suggested_text="Run focused tests before closeout.",
        suggested_patch="",
        target_content_hash="abc",
    )
    assert proposal.state == "staged"


def test_instruction_proposal_rejects_runtime_author_source() -> None:
    with pytest.raises(ValidationError):
        build_instruction_proposal(
            candidate_id="cand-1",
            target_file="/tmp/project/OPENMINION.md",
            target_name="OPENMINION.md",
            proposal_kind="append_bullet",
            summary="Bad source",
            evidence_refs=["trace:1"],
            author_source="runtime",  # type: ignore[arg-type]
            suggested_text="Never inferred locally.",
            target_content_hash="abc",
        )


def test_instruction_proposal_requires_authored_text_except_manual_review() -> None:
    with pytest.raises(ValidationError, match="authored_instruction_text"):
        build_instruction_proposal(
            candidate_id="cand-1",
            target_file="/tmp/project/OPENMINION.md",
            target_name="OPENMINION.md",
            proposal_kind="append_bullet",
            summary="Missing text",
            evidence_refs=["trace:1"],
            author_source="operator",
            suggested_text="",
            target_content_hash="abc",
        )

    proposal_hash = compute_instruction_proposal_hash(
        candidate_id="cand-2",
        target_file="/tmp/project/OPENMINION.md",
        target_name="OPENMINION.md",
        proposal_kind="manual_review",
        suggested_text="",
        suggested_patch="",
        target_content_hash="abc",
    )
    proposal = InstructionProposal(
        candidate_id="cand-2",
        target_file="/tmp/project/OPENMINION.md",
        target_name="OPENMINION.md",
        proposal_kind="manual_review",
        summary="Needs human text",
        evidence_refs=["trace:1"],
        author_source="operator",
        suggested_text="",
        target_content_hash="abc",
        proposal_hash=proposal_hash,
    )

    assert proposal.proposal_kind == "manual_review"


def test_instruction_proposal_rejects_hash_mismatch() -> None:
    with pytest.raises(ValidationError, match="proposal_hash_mismatch"):
        InstructionProposal(
            candidate_id="cand-1",
            target_file="/tmp/project/OPENMINION.md",
            target_name="OPENMINION.md",
            proposal_kind="append_bullet",
            summary="Hash mismatch",
            evidence_refs=["trace:1"],
            author_source="operator",
            suggested_text="Body",
            target_content_hash="abc",
            proposal_hash="wrong",
        )


def test_instruction_approval_record_requires_trusted_actor_and_session() -> None:
    with pytest.raises(ValidationError):
        InstructionApprovalRecord(
            approval_id="approval-1",
            candidate_id="cand-1",
            proposal_hash="hash",
            target_file="/tmp/project/OPENMINION.md",
            target_content_hash="abc",
            actor_id="",
            session_id="session-1",
            approval_source="cli_confirm",
        )
