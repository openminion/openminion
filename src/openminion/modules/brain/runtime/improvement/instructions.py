from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openminion.base.time import utc_now_iso
from openminion.modules.brain.runtime.improvement.candidates import (
    ImprovementCandidateState,
)

InstructionOpportunitySource = Literal[
    "self_improvement_note",
    "workflow_shape",
    "goal_proof",
    "operator_signal",
    "validation_failure",
]
InstructionProposalKind = Literal[
    "append_section",
    "replace_section",
    "append_bullet",
    "manual_review",
]
InstructionAuthorSource = Literal["llm", "operator", "imported"]
InstructionRiskLevel = Literal["low", "medium", "high"]
InstructionApprovalSource = Literal[
    "terminal_approval_callback",
    "cli_confirm",
    "api_approval",
    "test_helper",
]

INSTRUCTION_PROPOSAL_EVENT_TYPES: tuple[str, ...] = (
    "instruction.opportunity_staged",
    "instruction.proposal_authored",
    "instruction.proposal_staged",
    "instruction.approval_issued",
    "instruction.proposal_applied",
    "instruction.proposal_rejected",
    "instruction.rollback_completed",
    "instruction.rollback_review_required",
    "instruction.stale_target_rejected",
    "instruction.unsafe_path_blocked",
)


class InstructionOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opportunity_id: str
    source_kind: InstructionOpportunitySource
    evidence_refs: list[str] = Field(default_factory=list)
    observed_count: int = 1
    first_seen_at: str = Field(default_factory=utc_now_iso)
    last_seen_at: str = Field(default_factory=utc_now_iso)
    target_hint: str = ""
    needs_authoring: bool = True

    @field_validator("opportunity_id", "source_kind")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("field_required")
        return normalized

    @field_validator("observed_count")
    @classmethod
    def _positive_count(cls, value: int) -> int:
        if int(value) < 1:
            raise ValueError("observed_count_must_be_positive")
        return int(value)


class InstructionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    opportunity_id: str = ""
    target_file: str
    target_name: str
    proposal_kind: InstructionProposalKind
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)
    author_source: InstructionAuthorSource
    suggested_text: str = ""
    suggested_patch: str = ""
    risk_level: InstructionRiskLevel = "medium"
    review_mode: Literal["review_first"] = "review_first"
    validation_hint: str = ""
    target_content_hash: str
    proposal_hash: str
    state: ImprovementCandidateState = "staged"
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator(
        "candidate_id",
        "target_file",
        "target_name",
        "proposal_kind",
        "summary",
        "author_source",
        "target_content_hash",
        "proposal_hash",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("field_required")
        return normalized

    @model_validator(mode="after")
    def _validate_authored_payload(self) -> "InstructionProposal":
        has_text = bool(self.suggested_text.strip())
        has_patch = bool(self.suggested_patch.strip())
        if self.proposal_kind != "manual_review" and not (has_text or has_patch):
            raise ValueError("authored_instruction_text_or_patch_required")
        if self.proposal_kind == "manual_review" and has_patch:
            raise ValueError("manual_review_cannot_auto_apply_patch")
        expected_hash = compute_instruction_proposal_hash(
            candidate_id=self.candidate_id,
            target_file=self.target_file,
            target_name=self.target_name,
            proposal_kind=self.proposal_kind,
            suggested_text=self.suggested_text,
            suggested_patch=self.suggested_patch,
            target_content_hash=self.target_content_hash,
        )
        if self.proposal_hash != expected_hash:
            raise ValueError("proposal_hash_mismatch")
        return self

    def transition(self, state: ImprovementCandidateState) -> "InstructionProposal":
        return self.model_copy(update={"state": state, "updated_at": utc_now_iso()})


class InstructionApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str
    candidate_id: str
    proposal_hash: str
    target_file: str
    target_content_hash: str
    actor_id: str
    session_id: str
    approval_source: InstructionApprovalSource
    approved_at: str = Field(default_factory=utc_now_iso)
    expires_at: str = ""
    single_use: bool = True
    used_at: str = ""

    @field_validator(
        "approval_id",
        "candidate_id",
        "proposal_hash",
        "target_file",
        "target_content_hash",
        "actor_id",
        "session_id",
        "approval_source",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("field_required")
        return normalized

    @property
    def is_used(self) -> bool:
        return bool(self.used_at.strip())

    def mark_used(self) -> "InstructionApprovalRecord":
        return self.model_copy(update={"used_at": utc_now_iso()})


class InstructionTargetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_file: str
    target_name: str
    project_root: str
    content_hash: str
    newline: str = "\n"
    encoding: str = "utf-8"
    mode: int | None = None
    content: str = ""


class InstructionRollbackRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rollback_id: str
    candidate_id: str
    target_file: str
    before_content: str
    before_hash: str
    after_hash: str
    newline: str = "\n"
    encoding: str = "utf-8"
    mode: int | None = None
    created_at: str = Field(default_factory=utc_now_iso)


class InstructionProposalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_type: str
    candidate_id: str = ""
    approval_id: str = ""
    target_file: str = ""
    state: str = ""
    reason_code: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    recorded_at: str = Field(default_factory=utc_now_iso)

    @field_validator("event_type")
    @classmethod
    def _known_event_type(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if normalized not in INSTRUCTION_PROPOSAL_EVENT_TYPES:
            raise ValueError("unknown_instruction_event_type")
        return normalized


def compute_instruction_proposal_hash(
    *,
    candidate_id: str,
    target_file: str,
    target_name: str,
    proposal_kind: str,
    suggested_text: str,
    suggested_patch: str,
    target_content_hash: str,
) -> str:
    payload = {
        "candidate_id": str(candidate_id or "").strip(),
        "proposal_kind": str(proposal_kind or "").strip(),
        "suggested_patch": str(suggested_patch or ""),
        "suggested_text": str(suggested_text or ""),
        "target_content_hash": str(target_content_hash or "").strip(),
        "target_file": str(target_file or "").strip(),
        "target_name": str(target_name or "").strip(),
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_instruction_proposal(
    *,
    candidate_id: str,
    target_file: str,
    target_name: str,
    proposal_kind: InstructionProposalKind,
    summary: str,
    evidence_refs: list[str],
    author_source: InstructionAuthorSource,
    suggested_text: str,
    suggested_patch: str = "",
    target_content_hash: str,
    opportunity_id: str = "",
    risk_level: InstructionRiskLevel = "medium",
    validation_hint: str = "",
) -> InstructionProposal:
    proposal_hash = compute_instruction_proposal_hash(
        candidate_id=candidate_id,
        target_file=target_file,
        target_name=target_name,
        proposal_kind=proposal_kind,
        suggested_text=suggested_text,
        suggested_patch=suggested_patch,
        target_content_hash=target_content_hash,
    )
    return InstructionProposal(
        candidate_id=candidate_id,
        opportunity_id=opportunity_id,
        target_file=target_file,
        target_name=target_name,
        proposal_kind=proposal_kind,
        summary=summary,
        evidence_refs=evidence_refs,
        author_source=author_source,
        suggested_text=suggested_text,
        suggested_patch=suggested_patch,
        risk_level=risk_level,
        validation_hint=validation_hint,
        target_content_hash=target_content_hash,
        proposal_hash=proposal_hash,
    )


__all__ = [
    "INSTRUCTION_PROPOSAL_EVENT_TYPES",
    "InstructionApprovalRecord",
    "InstructionApprovalSource",
    "InstructionAuthorSource",
    "InstructionOpportunity",
    "InstructionOpportunitySource",
    "InstructionProposal",
    "InstructionProposalEvent",
    "InstructionProposalKind",
    "InstructionRollbackRef",
    "InstructionTargetSnapshot",
    "build_instruction_proposal",
    "compute_instruction_proposal_hash",
]
