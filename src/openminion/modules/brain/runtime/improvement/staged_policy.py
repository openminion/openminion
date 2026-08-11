"""Review-gated staged policy lifecycle for self-improvement candidates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import LoopPolicyPromotionVerdict

StagedPolicyTarget = Literal[
    "instruction",
    "skill",
    "plan",
    "tool_policy",
    "workflow",
    "context_policy",
]
StagedPolicyState = Literal[
    "candidate",
    "accepted",
    "rejected",
    "active",
    "ineffective",
    "rolled_back",
]
StagedPolicyTransitionStatus = Literal["allowed", "blocked"]
StagedPolicyActorKind = Literal["operator", "reviewer", "system_owner"]


class StagedPolicyCandidate(BaseModel):
    """One generated instruction, skill, plan, or policy awaiting review."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    target_type: StagedPolicyTarget
    target_owner: str
    proposed_change_ref: str
    state: StagedPolicyState = "candidate"
    provenance_refs: list[str] = Field(default_factory=list)
    replay_evidence_refs: list[str] = Field(default_factory=list)
    review_refs: list[str] = Field(default_factory=list)
    activation_refs: list[str] = Field(default_factory=list)
    effectiveness_refs: list[str] = Field(default_factory=list)
    rollback_refs: list[str] = Field(default_factory=list)


class StagedPolicyTransitionRequest(BaseModel):
    """A structural transition request; semantic approval stays outside runtime."""

    model_config = ConfigDict(extra="forbid")

    target_state: StagedPolicyState
    actor_kind: StagedPolicyActorKind
    review_ref: str = ""
    activation_ref: str = ""
    effectiveness_ref: str = ""
    rollback_ref: str = ""
    replay_verdict: LoopPolicyPromotionVerdict | None = None
    reason_code: str = ""


class StagedPolicyTransitionResult(BaseModel):
    """Result of evaluating one staged-policy lifecycle transition."""

    model_config = ConfigDict(extra="forbid")

    status: StagedPolicyTransitionStatus
    reason_code: str
    candidate: StagedPolicyCandidate


def evaluate_staged_policy_transition(
    candidate: StagedPolicyCandidate | Mapping[str, object],
    request: StagedPolicyTransitionRequest | Mapping[str, object],
) -> StagedPolicyTransitionResult:
    """Apply legal staged-policy lifecycle transitions with fail-closed gates."""

    candidate_obj = StagedPolicyCandidate.model_validate(candidate)
    request_obj = StagedPolicyTransitionRequest.model_validate(request)
    if not candidate_obj.provenance_refs:
        return _blocked(candidate_obj, "provenance_required")
    if request_obj.target_state == "accepted":
        return _accept(candidate_obj, request_obj)
    if request_obj.target_state == "rejected":
        return _reject(candidate_obj, request_obj)
    if request_obj.target_state == "active":
        return _activate(candidate_obj, request_obj)
    if request_obj.target_state == "ineffective":
        return _mark_ineffective(candidate_obj, request_obj)
    if request_obj.target_state == "rolled_back":
        return _roll_back(candidate_obj, request_obj)
    return _blocked(candidate_obj, "candidate_state_is_initial_only")


def _accept(
    candidate: StagedPolicyCandidate,
    request: StagedPolicyTransitionRequest,
) -> StagedPolicyTransitionResult:
    if candidate.state != "candidate":
        return _blocked(candidate, "accept_requires_candidate_state")
    if not request.review_ref:
        return _blocked(candidate, "review_ref_required")
    replay_verdict = request.replay_verdict
    if replay_verdict is None:
        return _blocked(candidate, "replay_verdict_required")
    if replay_verdict.candidate_id != candidate.candidate_id:
        return _blocked(candidate, "replay_candidate_mismatch")
    if replay_verdict.verdict != "promote":
        return _blocked(candidate, "negative_replay_cannot_activate")
    if not replay_verdict.evidence_refs:
        return _blocked(candidate, "replay_evidence_required")
    return _allowed(
        candidate.model_copy(
            update={
                "state": "accepted",
                "review_refs": _append(candidate.review_refs, request.review_ref),
                "replay_evidence_refs": _append_many(
                    candidate.replay_evidence_refs,
                    replay_verdict.evidence_refs,
                ),
            }
        ),
        "accepted_after_reviewed_positive_replay",
    )


def _activate(
    candidate: StagedPolicyCandidate,
    request: StagedPolicyTransitionRequest,
) -> StagedPolicyTransitionResult:
    if candidate.state != "accepted":
        return _blocked(candidate, "activation_requires_accepted_state")
    if not candidate.review_refs and not request.review_ref:
        return _blocked(candidate, "review_ref_required")
    if not request.activation_ref:
        return _blocked(candidate, "activation_ref_required")
    return _allowed(
        candidate.model_copy(
            update={
                "state": "active",
                "review_refs": _append(candidate.review_refs, request.review_ref),
                "activation_refs": _append(
                    candidate.activation_refs,
                    request.activation_ref,
                ),
            }
        ),
        "activated_after_review",
    )


def _mark_ineffective(
    candidate: StagedPolicyCandidate,
    request: StagedPolicyTransitionRequest,
) -> StagedPolicyTransitionResult:
    if candidate.state != "active":
        return _blocked(candidate, "ineffective_requires_active_state")
    if not request.effectiveness_ref:
        return _blocked(candidate, "effectiveness_ref_required")
    return _allowed(
        candidate.model_copy(
            update={
                "state": "ineffective",
                "effectiveness_refs": _append(
                    candidate.effectiveness_refs,
                    request.effectiveness_ref,
                ),
            }
        ),
        "ineffective_measurement_recorded",
    )


def _roll_back(
    candidate: StagedPolicyCandidate,
    request: StagedPolicyTransitionRequest,
) -> StagedPolicyTransitionResult:
    if candidate.state not in {"active", "ineffective"}:
        return _blocked(candidate, "rollback_requires_active_or_ineffective_state")
    if not request.rollback_ref:
        return _blocked(candidate, "rollback_ref_required")
    return _allowed(
        candidate.model_copy(
            update={
                "state": "rolled_back",
                "rollback_refs": _append(candidate.rollback_refs, request.rollback_ref),
            }
        ),
        "rollback_recorded",
    )


def _reject(
    candidate: StagedPolicyCandidate,
    request: StagedPolicyTransitionRequest,
) -> StagedPolicyTransitionResult:
    if not request.review_ref:
        return _blocked(candidate, "review_ref_required")
    return _allowed(
        candidate.model_copy(
            update={
                "state": "rejected",
                "review_refs": _append(candidate.review_refs, request.review_ref),
            }
        ),
        request.reason_code or "rejected_after_review",
    )


def _allowed(
    candidate: StagedPolicyCandidate,
    reason_code: str,
) -> StagedPolicyTransitionResult:
    return StagedPolicyTransitionResult(
        status="allowed",
        reason_code=reason_code,
        candidate=candidate,
    )


def _blocked(
    candidate: StagedPolicyCandidate,
    reason_code: str,
) -> StagedPolicyTransitionResult:
    return StagedPolicyTransitionResult(
        status="blocked",
        reason_code=reason_code,
        candidate=candidate,
    )


def _append(values: list[str], value: str) -> list[str]:
    out = list(values)
    value = value.strip()
    if value and value not in out:
        out.append(value)
    return out


def _append_many(values: list[str], new_values: list[str]) -> list[str]:
    out = list(values)
    for value in new_values:
        out = _append(out, value)
    return out


__all__ = [
    "StagedPolicyActorKind",
    "StagedPolicyCandidate",
    "StagedPolicyState",
    "StagedPolicyTarget",
    "StagedPolicyTransitionRequest",
    "StagedPolicyTransitionResult",
    "StagedPolicyTransitionStatus",
    "evaluate_staged_policy_transition",
]
