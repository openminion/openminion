from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.modules.brain.runtime.improvement.contracts import (
    SELF_IMPROVEMENT_POLICY_DISABLED,
    ImprovementDecision,
    OnlineImprovementEval,
    SelfImprovementPolicy,
)


def test_online_eval_minimal_required_fields() -> None:
    ev = OnlineImprovementEval(
        attempt_id="a1",
        trace_id="t1",
        mode_name="act",
        outcome_status="failure",
    )
    assert ev.tool_name == ""
    assert ev.iteration == 0
    assert ev.anomaly_score == 0.0
    assert ev.progress_delta == "unknown"
    assert ev.evidence_refs == []


def test_online_eval_rejects_unknown_outcome_status() -> None:
    with pytest.raises(ValidationError):
        OnlineImprovementEval(
            attempt_id="a1",
            trace_id="t1",
            mode_name="act",
            outcome_status="exploded",  # not in the Literal set
        )


def test_online_eval_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        OnlineImprovementEval(
            attempt_id="a1",
            trace_id="t1",
            mode_name="act",
            outcome_status="success",
            sneaky="x",  # extra=forbid
        )


def test_online_eval_rejects_negative_iteration() -> None:
    with pytest.raises(ValidationError):
        OnlineImprovementEval(
            attempt_id="a1",
            trace_id="t1",
            mode_name="act",
            outcome_status="success",
            iteration=-1,
        )


def test_decision_default_is_conservative_noop() -> None:
    d = ImprovementDecision()
    assert d.action == "ignore"
    assert d.memory_kind == "none"
    assert d.confidence == 0.0
    assert d.tags == []


@pytest.mark.parametrize(
    "memory_kind",
    [
        "lesson",
        "procedure",
        "preference",
        "goal_revision",
        "failure_pattern",
        "strategy_outcome",
        "none",
    ],
)
def test_decision_memory_kind_aligns_with_sophiagraph_kinds(memory_kind: str) -> None:
    # memory_kind must round-trip every Sophiagraph memory kind (plus `none`)
    # so the OpenMinion -> Sophiagraph staging handoff vocabulary matches.
    assert ImprovementDecision(memory_kind=memory_kind).memory_kind == memory_kind  # type: ignore[arg-type]


def test_decision_rejects_legacy_lesson_kind_field() -> None:
    # The pre-review field name was `lesson_kind`; extra=forbid must reject it
    # so stale callers fail loudly rather than silently dropping the value.
    with pytest.raises(ValidationError):
        ImprovementDecision(lesson_kind="lesson")


def test_decision_confidence_is_bounded() -> None:
    with pytest.raises(ValidationError):
        ImprovementDecision(confidence=1.5)
    with pytest.raises(ValidationError):
        ImprovementDecision(confidence=-0.1)
    assert ImprovementDecision(confidence=0.5).confidence == 0.5


def test_decision_rejects_unknown_action() -> None:
    with pytest.raises(ValidationError):
        ImprovementDecision(action="self_edit_code")  # forbidden by design


@pytest.mark.parametrize(
    "action",
    [
        "ignore",
        "retry_now",
        "replan_now",
        "ask_user",
        "stage_lesson",
        "stage_candidate",
    ],
)
def test_decision_accepts_each_allowed_action(action: str) -> None:
    assert ImprovementDecision(action=action).action == action  # type: ignore[arg-type]


def test_policy_default_is_disabled() -> None:
    p = SelfImprovementPolicy()
    assert p.policy == SELF_IMPROVEMENT_POLICY_DISABLED
    assert p.policy == "never"
    assert p.is_enabled is False


def test_policy_default_review_mode_is_review_first() -> None:
    # Even when later enabled, the default must not auto-promote.
    assert SelfImprovementPolicy().review_mode == "review_first"


def test_policy_default_budgets_are_minimal() -> None:
    p = SelfImprovementPolicy()
    assert p.reserved_llm_calls == 0
    assert p.max_staged_items_per_run == 0
    assert p.min_external_signal_count == 1


@pytest.mark.parametrize("policy", ["anomaly", "checkpoint", "post_run"])
def test_policy_is_enabled_for_active_policies(policy: str) -> None:
    p = SelfImprovementPolicy(policy=policy)  # type: ignore[arg-type]
    assert p.is_enabled is True


def test_policy_rejects_unknown_policy() -> None:
    with pytest.raises(ValidationError):
        SelfImprovementPolicy(policy="autonomous_rewrite")


def test_policy_rejects_negative_budgets() -> None:
    with pytest.raises(ValidationError):
        SelfImprovementPolicy(reserved_llm_calls=-1)
