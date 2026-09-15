from __future__ import annotations

import pytest

from openminion.base.config.parser import (
    openminion_config_from_dict,
    openminion_config_to_dict,
)
from openminion.base.config.parser.runtime import _build_runtime_config
from openminion.base.config.runtime import RuntimeConfig, ToolPolicyConfig
from openminion.modules.brain.config import RetryConfig, RunnerOptions
from openminion.modules.policy.runtime.security import ToolBudgetPolicy
from openminion.services.brain.metadata import resolve_agent_budgets


def test_ar01_agent_loop_max_steps_default_is_at_least_50() -> None:
    assert RuntimeConfig().agent_loop_max_steps >= 50, (
        "AR-01: production default for agent_loop_max_steps must be >= 50; "
        "see the autonomy / loop-reliability spec"
    )


def test_ar01_parser_uses_the_production_loop_default() -> None:
    assert (
        _build_runtime_config({}).agent_loop_max_steps
        == RuntimeConfig().agent_loop_max_steps
    )


def test_ar02_retry_config_max_replans_default_is_at_least_8() -> None:
    assert RetryConfig().max_replans >= 8, (
        "AR-02: RetryConfig.max_replans default must be >= 8; "
        "see autonomy / loop-reliability spec"
    )


def test_ar02_runner_options_max_replans_default_aligned() -> None:
    assert RunnerOptions().max_replans == RetryConfig().max_replans, (
        "AR-02: RunnerOptions.max_replans must track RetryConfig.max_replans "
        "so both adaptive-loop entrypoints share the same budget"
    )


def test_ar03_tool_policy_max_calls_per_run_default_is_at_least_50() -> None:
    assert ToolPolicyConfig().max_calls_per_run >= 50, (
        "AR-03: ToolPolicyConfig.max_calls_per_run default must be >= 50; "
        "see autonomy / loop-reliability spec"
    )


def test_budget_envelope_constants_are_operator_overridable() -> None:
    cfg = RuntimeConfig(agent_loop_max_steps=4)
    assert cfg.agent_loop_max_steps == 4

    retries = RetryConfig(max_replans=2)
    assert retries.max_replans == 2

    pol = ToolPolicyConfig(max_calls_per_run=8)
    assert pol.max_calls_per_run == 8


@pytest.mark.parametrize(
    ("runtime", "policy", "expected"),
    [
        ({}, {}, (100, 300, 100, 50, 200)),
        ({}, {"max_calls_per_tool": 2}, (100, 300, 100, 2, 200)),
        (
            {"agent_loop_max_steps": 4, "brain_turn_timeout_seconds": 120},
            {
                "max_calls_per_run": 8,
                "max_calls_per_tool": 4,
                "max_budget_cost_per_run": 16,
            },
            (4, 120, 8, 4, 16),
        ),
    ],
)
def test_parsed_budget_defaults_and_overrides_reach_brain(
    runtime, policy, expected
) -> None:
    config = openminion_config_from_dict(
        {
            "agents": {"default": {"provider": "echo"}},
            "runtime": runtime,
            "security": {"tool_policy": policy},
        }
    )
    steps, seconds, calls, per_tool, cost = expected
    assert config.runtime.agent_loop_max_steps == steps
    assert config.runtime.brain_turn_timeout_seconds == seconds
    assert config.security.tool_policy == ToolPolicyConfig(
        max_calls_per_run=calls,
        max_calls_per_tool=per_tool,
        max_budget_cost_per_run=cost,
    )
    restored = openminion_config_from_dict(openminion_config_to_dict(config))
    budgets = resolve_agent_budgets(restored, override_value=lambda _: "")
    assert budgets.max_ticks_per_user_turn == steps
    assert budgets.max_tool_calls == calls
    assert budgets.max_elapsed_ms == seconds * 1000
    assert restored.security.tool_policy == config.security.tool_policy


def test_direct_and_standalone_defaults_match_shared_budget() -> None:
    assert RuntimeConfig().agent_loop_max_steps == 100
    assert RuntimeConfig().brain_turn_timeout_seconds == 300
    assert ToolPolicyConfig().max_calls_per_run == 100
    assert ToolPolicyConfig().max_calls_per_tool == 50
    assert ToolPolicyConfig().max_budget_cost_per_run == 200
    assert ToolBudgetPolicy() == ToolBudgetPolicy(
        max_calls_per_run=100, max_calls_per_tool=50, max_budget_cost_per_run=200
    )
