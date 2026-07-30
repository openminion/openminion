from __future__ import annotations

from pydantic import ValidationError
import pytest

from openminion.modules.brain.runtime.improvement.contracts import (
    LoopPolicyPromotionVerdict,
)
from openminion.modules.brain.runtime.improvement.staged_policy import (
    StagedPolicyCandidate,
    StagedPolicyTransitionRequest,
    evaluate_staged_policy_transition,
)


def _candidate(**updates: object) -> StagedPolicyCandidate:
    data = {
        "candidate_id": "cand-1",
        "target_type": "instruction",
        "target_owner": "brain.runtime.improvement",
        "proposed_change_ref": "proposal:cand-1",
        "provenance_refs": ["trace:1"],
    }
    data.update(updates)
    return StagedPolicyCandidate.model_validate(data)


def _verdict(verdict: str = "promote") -> LoopPolicyPromotionVerdict:
    return LoopPolicyPromotionVerdict(
        candidate_id="cand-1",
        verdict=verdict,
        reason_code=f"candidate_{verdict}",
        supporting_metrics={"baseline.success_rate": 0.4},
        evidence_refs=["replay:1"],
    )


def test_staged_policy_lifecycle_requires_review_before_activation() -> None:
    accepted = evaluate_staged_policy_transition(
        _candidate(),
        StagedPolicyTransitionRequest(
            target_state="accepted",
            actor_kind="reviewer",
            review_ref="review:1",
            replay_verdict=_verdict(),
        ),
    )
    assert accepted.status == "allowed"
    assert accepted.candidate.state == "accepted"
    assert accepted.candidate.review_refs == ["review:1"]
    assert accepted.candidate.replay_evidence_refs == ["replay:1"]

    active = evaluate_staged_policy_transition(
        accepted.candidate,
        StagedPolicyTransitionRequest(
            target_state="active",
            actor_kind="system_owner",
            activation_ref="activation:1",
        ),
    )
    assert active.status == "allowed"
    assert active.candidate.state == "active"
    assert active.candidate.activation_refs == ["activation:1"]


def test_negative_replay_cannot_accept_or_activate_candidate() -> None:
    result = evaluate_staged_policy_transition(
        _candidate(),
        StagedPolicyTransitionRequest(
            target_state="accepted",
            actor_kind="reviewer",
            review_ref="review:1",
            replay_verdict=_verdict("rollback"),
        ),
    )

    assert result.status == "blocked"
    assert result.reason_code == "negative_replay_cannot_activate"
    assert result.candidate.state == "candidate"


def test_direct_activation_from_candidate_is_blocked() -> None:
    result = evaluate_staged_policy_transition(
        _candidate(),
        StagedPolicyTransitionRequest(
            target_state="active",
            actor_kind="system_owner",
            activation_ref="activation:1",
        ),
    )

    assert result.status == "blocked"
    assert result.reason_code == "activation_requires_accepted_state"


def test_ineffective_and_rollback_require_durable_evidence_refs() -> None:
    accepted = evaluate_staged_policy_transition(
        _candidate(),
        {
            "target_state": "accepted",
            "actor_kind": "reviewer",
            "review_ref": "review:1",
            "replay_verdict": _verdict(),
        },
    ).candidate
    active = evaluate_staged_policy_transition(
        accepted,
        {
            "target_state": "active",
            "actor_kind": "system_owner",
            "activation_ref": "activation:1",
        },
    ).candidate

    missing_effectiveness = evaluate_staged_policy_transition(
        active,
        {"target_state": "ineffective", "actor_kind": "system_owner"},
    )
    assert missing_effectiveness.status == "blocked"
    assert missing_effectiveness.reason_code == "effectiveness_ref_required"

    ineffective = evaluate_staged_policy_transition(
        active,
        {
            "target_state": "ineffective",
            "actor_kind": "system_owner",
            "effectiveness_ref": "effectiveness:1",
        },
    )
    assert ineffective.status == "allowed"
    assert ineffective.candidate.state == "ineffective"
    assert ineffective.candidate.effectiveness_refs == ["effectiveness:1"]

    rolled_back = evaluate_staged_policy_transition(
        ineffective.candidate,
        {
            "target_state": "rolled_back",
            "actor_kind": "system_owner",
            "rollback_ref": "rollback:1",
        },
    )
    assert rolled_back.status == "allowed"
    assert rolled_back.candidate.state == "rolled_back"
    assert rolled_back.candidate.rollback_refs == ["rollback:1"]


def test_staged_policy_contract_rejects_runtime_semantic_fields() -> None:
    with pytest.raises(ValidationError):
        StagedPolicyTransitionRequest.model_validate(
            {
                "target_state": "accepted",
                "actor_kind": "runtime",
                "semantic_reason": "looks better",
            }
        )
