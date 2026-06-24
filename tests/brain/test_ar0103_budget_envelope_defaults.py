from __future__ import annotations

from openminion.base.config.runtime import RuntimeConfig, ToolPolicyConfig
from openminion.modules.brain.config import RetryConfig, RunnerOptions


def test_ar01_agent_loop_max_steps_default_is_at_least_50() -> None:
    assert RuntimeConfig().agent_loop_max_steps >= 50, (
        "AR-01: production default for agent_loop_max_steps must be >= 50; "
        "see the autonomy / loop-reliability spec"
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
