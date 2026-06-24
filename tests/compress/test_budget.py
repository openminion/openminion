import pytest

from openminion.modules.context.compress.budget import BudgetPlanner
from openminion.modules.context.compress.errors import BudgetError
from openminion.modules.context.compress.schemas import CompressionBudgets


def test_budget_planner_enforces_reserve_and_type_caps():
    planner = BudgetPlanner()
    budgets = CompressionBudgets(
        max_output_tokens_total=512,
        max_output_tokens_by_type={"retrieval": 256},
        reserve_tokens_for_headers=32,
        hard_cap=True,
    )

    envelope = planner.plan(budgets)
    assert envelope.total_cap == 480
    assert envelope.per_type_caps["retrieval"] == 256

    state = planner.new_state(envelope)
    state.try_allocate("retrieval", 200)
    assert state.used_total == 200

    with pytest.raises(BudgetError):
        state.try_allocate("retrieval", 100)


def test_budget_planner_rejects_invalid_totals():
    planner = BudgetPlanner()
    budgets = CompressionBudgets(
        max_output_tokens_total=16,
        reserve_tokens_for_headers=16,
        hard_cap=True,
    )

    with pytest.raises(BudgetError):
        planner.plan(budgets)

    budgets = CompressionBudgets(max_output_tokens_total=0)
    with pytest.raises(BudgetError):
        planner.plan(budgets)
