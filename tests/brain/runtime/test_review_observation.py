from __future__ import annotations

from dataclasses import dataclass

import pytest

from openminion.modules.brain.runtime.review.observation import (
    REVIEW_BLOCK_REASON,
    REVIEW_TOOL_NAME,
    apply_review_to_judgment,
    is_review_blocking,
    observe_review_invocation,
)
from openminion.modules.brain.schemas.closure import ClosureJudgment, ReviewFact


def _review_result_entry(
    *, findings_count: int, severity: str, ok: bool = True
) -> dict:
    return {
        "tool_name": REVIEW_TOOL_NAME,
        "ok": ok,
        "data": {
            "findings_count": findings_count,
            "severity": severity,
        },
    }


def _other_tool_entry() -> dict:
    return {"tool_name": "exec.run", "ok": True, "data": {"argv": ["ls"]}}


@dataclass
class _StubBudgets:
    tool_calls: int = 10
    tokens: int = 1000
    time_ms: int = 60_000


@dataclass
class _StubState:
    budgets_remaining: _StubBudgets


def _state(**overrides) -> _StubState:
    return _StubState(budgets_remaining=_StubBudgets(**overrides))


@pytest.mark.parametrize(
    "results",
    [
        None,
        [],
        [_other_tool_entry(), _other_tool_entry()],
        ["broken", 123],
    ],
)
def test_returns_unavailable_when_no_review_invocation_is_present(
    results: object,
) -> None:
    fact = observe_review_invocation(results)
    assert isinstance(fact, ReviewFact)
    assert fact.invoked is False
    assert fact.findings_count == 0
    assert fact.severity == "unavailable"


@pytest.mark.parametrize(
    ("findings_count", "severity"),
    [(0, "ok"), (3, "warn"), (1, "block")],
)
def test_records_invocation_with_expected_severity(
    findings_count: int, severity: str
) -> None:
    results = [_review_result_entry(findings_count=findings_count, severity=severity)]
    fact = observe_review_invocation(results)
    assert fact.invoked is True
    assert fact.findings_count == findings_count
    assert fact.severity == severity


def test_invocation_records_when_only_review_tool_present() -> None:
    results = [_review_result_entry(findings_count=2, severity="warn")]
    fact = observe_review_invocation(results)
    assert fact.invoked is True


def test_invocation_records_when_mixed_with_other_tools() -> None:
    results = [
        _other_tool_entry(),
        _review_result_entry(findings_count=2, severity="warn"),
        _other_tool_entry(),
    ]
    fact = observe_review_invocation(results)
    assert fact.invoked is True
    assert fact.severity == "warn"


def test_last_successful_invocation_wins() -> None:
    results = [
        _review_result_entry(findings_count=5, severity="warn"),
        _review_result_entry(findings_count=0, severity="ok"),
    ]
    fact = observe_review_invocation(results)
    assert fact.severity == "ok"
    assert fact.findings_count == 0


def test_falls_back_to_last_entry_when_all_failed() -> None:
    results = [
        _review_result_entry(findings_count=0, severity="ok", ok=False),
        _review_result_entry(findings_count=2, severity="warn", ok=False),
    ]
    fact = observe_review_invocation(results)
    assert fact.invoked is True
    assert fact.severity == "warn"


def test_handles_findings_count_as_string_digits() -> None:
    results = [
        {
            "tool_name": REVIEW_TOOL_NAME,
            "ok": True,
            "data": {"findings_count": "7", "severity": "warn"},
        }
    ]
    fact = observe_review_invocation(results)
    assert fact.findings_count == 7


def test_handles_missing_findings_count() -> None:
    results = [
        {
            "tool_name": REVIEW_TOOL_NAME,
            "ok": True,
            "data": {"severity": "ok"},
        }
    ]
    fact = observe_review_invocation(results)
    assert fact.invoked is True
    assert fact.findings_count == 0
    assert fact.severity == "ok"


def test_handles_missing_data_dict_with_ok() -> None:
    results = [{"tool_name": REVIEW_TOOL_NAME, "ok": True}]
    fact = observe_review_invocation(results)
    assert fact.invoked is True
    assert fact.severity == "ok"


def test_handles_invalid_severity_value() -> None:
    results = [
        {
            "tool_name": REVIEW_TOOL_NAME,
            "ok": True,
            "data": {"severity": "nonsense"},
        }
    ]
    fact = observe_review_invocation(results)
    assert fact.invoked is True
    assert fact.severity == "ok"


def test_tool_name_match_is_case_insensitive() -> None:
    results = [
        {
            "tool_name": "REVIEW.DIFF",
            "ok": True,
            "data": {"findings_count": 1, "severity": "warn"},
        }
    ]
    fact = observe_review_invocation(results)
    assert fact.invoked is True


@pytest.mark.parametrize(
    ("fact", "expected"),
    [
        (ReviewFact(invoked=True, findings_count=1, severity="block"), True),
        (ReviewFact(invoked=True, findings_count=2, severity="warn"), False),
        (ReviewFact(invoked=True, findings_count=0, severity="ok"), False),
        (ReviewFact(), False),
        (None, False),
    ],
)
def test_is_review_blocking_predicate(fact: ReviewFact | None, expected: bool) -> None:
    assert is_review_blocking(fact) is expected


def _close_judgment(*, reason: str = "ok") -> ClosureJudgment:
    return ClosureJudgment(
        satisfied=True, reason=reason, next_action="close", final_answer="done."
    )


def test_attaches_ok_fact_without_override() -> None:
    judgment = _close_judgment()
    fact = ReviewFact(invoked=True, findings_count=0, severity="ok")
    apply_review_to_judgment(judgment, fact, state=_state())
    assert judgment.review is fact
    assert judgment.next_action == "close"
    assert REVIEW_BLOCK_REASON not in judgment.reason


def test_attaches_warn_fact_without_override() -> None:
    judgment = _close_judgment()
    fact = ReviewFact(invoked=True, findings_count=3, severity="warn")
    apply_review_to_judgment(judgment, fact, state=_state())
    assert judgment.review is fact
    assert judgment.next_action == "close"
    assert REVIEW_BLOCK_REASON not in judgment.reason


def test_attaches_block_fact_and_forces_continue_with_budget() -> None:
    judgment = _close_judgment()
    fact = ReviewFact(invoked=True, findings_count=1, severity="block")
    apply_review_to_judgment(judgment, fact, state=_state())
    assert judgment.review is fact
    assert judgment.satisfied is False
    assert judgment.next_action == "continue"
    assert judgment.final_answer is None
    assert REVIEW_BLOCK_REASON in judgment.reason


def test_attaches_block_fact_but_finalizes_without_budget() -> None:
    judgment = _close_judgment()
    fact = ReviewFact(invoked=True, findings_count=1, severity="block")
    apply_review_to_judgment(judgment, fact, state=_state(tokens=0))
    assert judgment.review is fact
    assert judgment.next_action == "close"
    assert REVIEW_BLOCK_REASON in judgment.reason


def test_unavailable_fact_does_not_override() -> None:
    judgment = _close_judgment()
    fact = ReviewFact()  # not invoked
    apply_review_to_judgment(judgment, fact, state=_state())
    assert judgment.next_action == "close"
    assert REVIEW_BLOCK_REASON not in judgment.reason


def test_block_does_not_fire_when_judge_already_continue() -> None:
    judgment = ClosureJudgment(
        satisfied=False, reason="prior", next_action="continue", final_answer=None
    )
    fact = ReviewFact(invoked=True, findings_count=1, severity="block")
    apply_review_to_judgment(judgment, fact, state=_state())
    assert judgment.next_action == "continue"
    assert REVIEW_BLOCK_REASON not in judgment.reason


def test_reason_composition_with_prior_reason() -> None:
    judgment = _close_judgment(reason="judge_complete")
    fact = ReviewFact(invoked=True, findings_count=1, severity="block")
    apply_review_to_judgment(judgment, fact, state=_state())
    assert judgment.reason == f"judge_complete; {REVIEW_BLOCK_REASON}"


def test_reason_set_without_separator_when_empty() -> None:
    judgment = ClosureJudgment(
        satisfied=True, reason="", next_action="close", final_answer="x"
    )
    fact = ReviewFact(invoked=True, findings_count=1, severity="block")
    apply_review_to_judgment(judgment, fact, state=_state())
    assert judgment.reason == REVIEW_BLOCK_REASON


def test_helper_returns_judgment_for_fluent_use() -> None:
    judgment = _close_judgment()
    returned = apply_review_to_judgment(judgment, ReviewFact(), state=_state())
    assert returned is judgment
