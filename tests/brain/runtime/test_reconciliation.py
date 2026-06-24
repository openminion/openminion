from __future__ import annotations

import pytest

from openminion.modules.brain.runtime.reconciliation import (
    PLAN_RECONCILIATION_STEP_ID_DIAG_CAP,
    PLAN_RECONCILIATION_TERMINAL_STATUSES,
    evaluate_plan_reconciliation,
    is_plan_reconciliation_incomplete,
)
from openminion.modules.brain.schemas.closure import PlanReconciliationFact


@pytest.mark.parametrize(
    "plan",
    [
        None,
        "not-a-plan",
        {"plan_id": "p1"},
        {"plan_id": "p1", "steps": []},
        {"plan_id": "p1", "steps": "broken"},
    ],
)
def test_returns_complete_for_non_step_inputs(plan: object) -> None:
    fact = evaluate_plan_reconciliation(plan)
    assert isinstance(fact, PlanReconciliationFact)
    assert fact.state == "complete"
    assert fact.unreconciled_items == 0
    assert fact.unreconciled_step_ids == ()


@pytest.mark.parametrize(
    "steps",
    [
        [
            {"step_id": "s1", "status": "completed"},
            {"step_id": "s2", "status": "completed"},
        ],
        [
            {"step_id": "s1", "status": "blocked"},
            {"step_id": "s2", "status": "blocked"},
        ],
        [
            {"step_id": "s1", "status": "completed"},
            {"step_id": "s2", "status": "blocked"},
            {"step_id": "s3", "status": "cancelled"},
        ],
    ],
)
def test_returns_complete_when_all_steps_are_terminal(
    steps: list[dict[str, str]],
) -> None:
    plan = {"plan_id": "p1", "steps": steps}
    fact = evaluate_plan_reconciliation(plan)
    assert fact.state == "complete"
    assert fact.unreconciled_items == 0
    assert fact.unreconciled_step_ids == ()


def test_terminal_status_set_pins_contract() -> None:
    assert PLAN_RECONCILIATION_TERMINAL_STATUSES == frozenset(
        {"completed", "blocked", "cancelled"}
    )


@pytest.mark.parametrize(
    ("plan", "expected_ids"),
    [
        (
            {
                "plan_id": "p1",
                "steps": [
                    {"step_id": "s1", "status": "completed"},
                    {"step_id": "s2", "status": "pending"},
                ],
            },
            ("s2",),
        ),
        (
            {
                "plan_id": "p1",
                "steps": [
                    {"step_id": "s1", "status": "in_progress"},
                    {"step_id": "s2", "status": "completed"},
                ],
            },
            ("s1",),
        ),
        (
            {
                "plan_id": "p1",
                "steps": [
                    {"step_id": "s1", "status": "pending"},
                    {"step_id": "s2", "status": "pending"},
                    {"step_id": "s3", "status": "in_progress"},
                ],
            },
            ("s1", "s2", "s3"),
        ),
        (
            {
                "plan_id": "p1",
                "steps": [
                    {"step_id": "s1"},
                    {"step_id": "s2", "status": "completed"},
                ],
            },
            ("s1",),
        ),
    ],
)
def test_returns_incomplete_for_non_terminal_steps(
    plan: dict[str, object], expected_ids: tuple[str, ...]
) -> None:
    fact = evaluate_plan_reconciliation(plan)
    expected_count = len(expected_ids)
    assert fact.state == "incomplete"
    assert fact.unreconciled_items == expected_count
    if expected_count == 1:
        assert fact.unreconciled_step_ids == expected_ids
    else:
        assert set(fact.unreconciled_step_ids) == set(expected_ids)


def test_status_case_insensitive() -> None:
    plan = {
        "plan_id": "p1",
        "steps": [
            {"step_id": "s1", "status": "COMPLETED"},
            {"step_id": "s2", "status": "Blocked"},
        ],
    }
    fact = evaluate_plan_reconciliation(plan)
    assert fact.state == "complete"


def test_unreconciled_step_ids_diagnostic_capped() -> None:
    cap = PLAN_RECONCILIATION_STEP_ID_DIAG_CAP
    over = cap + 5
    plan = {
        "plan_id": "p1",
        "steps": [{"step_id": f"s{i}", "status": "pending"} for i in range(over)],
    }
    fact = evaluate_plan_reconciliation(plan)
    assert fact.state == "incomplete"
    assert fact.unreconciled_items == over
    assert len(fact.unreconciled_step_ids) == cap


def test_step_with_missing_step_id_renders_unknown() -> None:
    plan = {
        "plan_id": "p1",
        "steps": [
            {"status": "pending"},
            {"step_id": "s2", "status": "completed"},
        ],
    }
    fact = evaluate_plan_reconciliation(plan)
    assert fact.state == "incomplete"
    assert fact.unreconciled_step_ids == ("<unknown>",)


def test_non_dict_step_entries_are_skipped() -> None:
    plan = {
        "plan_id": "p1",
        "steps": [
            "broken-entry",
            {"step_id": "s1", "status": "completed"},
        ],
    }
    fact = evaluate_plan_reconciliation(plan)
    assert fact.state == "complete"


def test_is_incomplete_predicate_true_for_incomplete_fact() -> None:
    fact = PlanReconciliationFact(
        state="incomplete", unreconciled_items=1, unreconciled_step_ids=("s1",)
    )
    assert is_plan_reconciliation_incomplete(fact) is True


def test_is_incomplete_predicate_false_for_complete_fact() -> None:
    fact = PlanReconciliationFact()
    assert is_plan_reconciliation_incomplete(fact) is False


def test_is_incomplete_predicate_false_for_none() -> None:
    assert is_plan_reconciliation_incomplete(None) is False
