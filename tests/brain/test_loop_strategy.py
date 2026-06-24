from __future__ import annotations

from openminion.modules.brain.constants import (
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_ACT_PROFILE_ORCHESTRATE,
    BRAIN_ACT_PROFILE_RESEARCH,
    BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
    BRAIN_INTERNAL_MODE_ACT_CODING,
    BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE,
    BRAIN_INTERNAL_MODE_ACT_RESEARCH,
    BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED,
)
from openminion.modules.brain.bootstrap.strategy import (
    CODING_LOOP_STRATEGY,
    DELEGATED_TARGET_STRATEGY,
    GENERAL_LOOP_STRATEGY,
    ORCHESTRATE_DISPATCH_STRATEGY,
    RESEARCH_LOOP_STRATEGY,
    resolve_loop_strategy,
)
from openminion.modules.brain.loop.strategies.coding.contracts import (
    CODING_ALLOWED_TOOLS,
)


def test_resolve_loop_strategy_defaults_to_general() -> None:
    strategy = resolve_loop_strategy(None)

    assert strategy is GENERAL_LOOP_STRATEGY
    assert strategy.mode_name == BRAIN_INTERNAL_MODE_ACT_ADAPTIVE
    assert strategy.route_kind == "loop"


def test_resolve_loop_strategy_returns_coding_strategy() -> None:
    strategy = resolve_loop_strategy(BRAIN_ACT_PROFILE_CODING)

    assert strategy is CODING_LOOP_STRATEGY
    assert strategy.mode_name == BRAIN_INTERNAL_MODE_ACT_CODING
    assert strategy.allowed_tools == CODING_ALLOWED_TOOLS


def test_resolve_loop_strategy_returns_research_strategy() -> None:
    strategy = resolve_loop_strategy(BRAIN_ACT_PROFILE_RESEARCH)

    assert strategy is RESEARCH_LOOP_STRATEGY
    assert strategy.mode_name == BRAIN_INTERNAL_MODE_ACT_RESEARCH
    assert strategy.checkpoint_support is True


def test_resolve_loop_strategy_routes_orchestrate_to_delegation() -> None:
    strategy = resolve_loop_strategy(BRAIN_ACT_PROFILE_ORCHESTRATE)

    assert strategy is ORCHESTRATE_DISPATCH_STRATEGY
    assert strategy.mode_name == BRAIN_INTERNAL_MODE_ACT_ORCHESTRATE
    assert strategy.route_kind == "delegation"


def test_delegated_target_strategy_routes_to_delegation_service() -> None:
    assert DELEGATED_TARGET_STRATEGY.mode_name == (
        BRAIN_INTERNAL_MODE_EXECUTION_TARGET_DELEGATED
    )
    assert DELEGATED_TARGET_STRATEGY.route_kind == "delegation"
