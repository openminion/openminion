from __future__ import annotations

from dataclasses import dataclass

from openminion.modules.brain.runtime.reconciliation import (
    PLAN_RECONCILIATION_INCOMPLETE_REASON,
    apply_plan_reconciliation_to_judgment,
)
from openminion.modules.brain.schemas.closure import (
    ClosureJudgment,
    PlanReconciliationFact,
)


@dataclass
class _StubBudgets:
    tool_calls: int = 10
    tokens: int = 1000
    time_ms: int = 60_000


@dataclass
class _StubState:
    budgets_remaining: _StubBudgets


def _close_judgment(*, reason: str = "ok") -> ClosureJudgment:
    return ClosureJudgment(
        satisfied=True,
        reason=reason,
        next_action="close",
        final_answer="done.",
    )


def _incomplete_fact(*, items: int = 1) -> PlanReconciliationFact:
    return PlanReconciliationFact(
        state="incomplete",
        unreconciled_items=items,
        unreconciled_step_ids=tuple(f"s{i}" for i in range(items)),
    )


def _complete_fact() -> PlanReconciliationFact:
    return PlanReconciliationFact()


def _state_with_budget(**overrides) -> _StubState:
    return _StubState(budgets_remaining=_StubBudgets(**overrides))


def test_attaches_complete_fact_without_overriding() -> None:
    judgment = _close_judgment()
    apply_plan_reconciliation_to_judgment(
        judgment, _complete_fact(), state=_state_with_budget()
    )
    assert judgment.plan_reconciliation is not None
    assert judgment.plan_reconciliation.state == "complete"
    assert judgment.satisfied is True
    assert judgment.next_action == "close"
    assert judgment.final_answer == "done."
    assert judgment.reason == "ok"
    assert PLAN_RECONCILIATION_INCOMPLETE_REASON not in judgment.reason


def test_attaches_incomplete_fact_and_overrides_when_budget_remains() -> None:
    judgment = _close_judgment()
    apply_plan_reconciliation_to_judgment(
        judgment, _incomplete_fact(items=2), state=_state_with_budget()
    )
    assert judgment.plan_reconciliation is not None
    assert judgment.plan_reconciliation.state == "incomplete"
    assert judgment.plan_reconciliation.unreconciled_items == 2
    assert judgment.satisfied is False
    assert judgment.next_action == "continue"
    assert judgment.final_answer is None
    assert PLAN_RECONCILIATION_INCOMPLETE_REASON in judgment.reason
    assert judgment.reason.startswith("ok; ")


def test_attaches_incomplete_fact_but_finalizes_when_budget_exhausted() -> None:
    judgment = _close_judgment()
    apply_plan_reconciliation_to_judgment(
        judgment, _incomplete_fact(), state=_state_with_budget(tool_calls=0)
    )
    assert judgment.plan_reconciliation is not None
    assert judgment.plan_reconciliation.state == "incomplete"
    assert judgment.satisfied is True
    assert judgment.next_action == "close"
    assert judgment.final_answer == "done."
    assert PLAN_RECONCILIATION_INCOMPLETE_REASON in judgment.reason


def test_does_not_override_when_judge_already_proposed_continue() -> None:
    judgment = ClosureJudgment(
        satisfied=False,
        reason="judge_replan",
        next_action="continue",
        final_answer=None,
    )
    apply_plan_reconciliation_to_judgment(
        judgment, _incomplete_fact(), state=_state_with_budget()
    )
    assert judgment.plan_reconciliation is not None
    assert judgment.satisfied is False
    assert judgment.next_action == "continue"
    assert PLAN_RECONCILIATION_INCOMPLETE_REASON not in judgment.reason


def test_does_not_override_when_judge_proposed_replan() -> None:
    judgment = ClosureJudgment(
        satisfied=False,
        reason="judge_replan",
        next_action="replan",
        final_answer=None,
    )
    apply_plan_reconciliation_to_judgment(
        judgment, _incomplete_fact(), state=_state_with_budget()
    )
    assert judgment.plan_reconciliation is not None
    assert judgment.next_action == "replan"
    assert PLAN_RECONCILIATION_INCOMPLETE_REASON not in judgment.reason


def test_does_not_override_when_satisfied_but_close_with_unsatisfied_flag_inconsistent() -> (
    None
):
    judgment = ClosureJudgment(
        satisfied=True,
        reason="ok",
        next_action="continue",
        final_answer=None,
    )
    apply_plan_reconciliation_to_judgment(
        judgment, _incomplete_fact(), state=_state_with_budget()
    )
    assert judgment.plan_reconciliation is not None
    assert judgment.next_action == "continue"
    assert PLAN_RECONCILIATION_INCOMPLETE_REASON not in judgment.reason


def test_reason_appended_with_separator_when_existing_reason() -> None:
    judgment = _close_judgment(reason="judge_complete")
    apply_plan_reconciliation_to_judgment(
        judgment, _incomplete_fact(), state=_state_with_budget()
    )
    assert judgment.reason == f"judge_complete; {PLAN_RECONCILIATION_INCOMPLETE_REASON}"


def test_reason_set_without_separator_when_empty_reason() -> None:
    judgment = ClosureJudgment(
        satisfied=True, reason="", next_action="close", final_answer="x"
    )
    apply_plan_reconciliation_to_judgment(
        judgment, _incomplete_fact(), state=_state_with_budget()
    )
    assert judgment.reason == PLAN_RECONCILIATION_INCOMPLETE_REASON


def test_zero_tokens_treated_as_no_budget() -> None:
    judgment = _close_judgment()
    apply_plan_reconciliation_to_judgment(
        judgment, _incomplete_fact(), state=_state_with_budget(tokens=0)
    )
    assert judgment.next_action == "close"
    assert PLAN_RECONCILIATION_INCOMPLETE_REASON in judgment.reason


def test_zero_time_ms_treated_as_no_budget() -> None:
    judgment = _close_judgment()
    apply_plan_reconciliation_to_judgment(
        judgment, _incomplete_fact(), state=_state_with_budget(time_ms=0)
    )
    assert judgment.next_action == "close"
    assert PLAN_RECONCILIATION_INCOMPLETE_REASON in judgment.reason


def test_missing_budgets_attribute_treated_as_no_budget() -> None:
    @dataclass
    class _BareState:
        pass

    judgment = _close_judgment()
    apply_plan_reconciliation_to_judgment(
        judgment, _incomplete_fact(), state=_BareState()
    )
    assert judgment.next_action == "close"
    assert PLAN_RECONCILIATION_INCOMPLETE_REASON in judgment.reason


def test_helper_returns_the_same_judgment_for_fluent_use() -> None:
    judgment = _close_judgment()
    returned = apply_plan_reconciliation_to_judgment(
        judgment, _complete_fact(), state=_state_with_budget()
    )
    assert returned is judgment
