from pathlib import Path

import yaml

from openminion.base.config import load_config


ROOT = Path(__file__).resolve().parents[2]


def test_focus_ci_is_independent_and_preserves_harness_coverage() -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader
    )
    job = workflow["jobs"]["focus-e2e"]
    assert "needs" not in job
    assert "if" not in job
    assert any(step.get("run") == "make test-e2e-ci" for step in job["steps"])
    assert not any(
        step.get("run") == "make test-e2e-ci"
        for step in workflow["jobs"]["test"]["steps"]
    )
    target = (
        (ROOT / "Makefile")
        .read_text()
        .split("test-e2e-ci:", 1)[1]
        .split("ci-check:", 1)[0]
    )
    for owner in (
        "cli/focus/test_local.py",
        "project_worker/test_completion.py",
        "test_memory_capture_recall_reliability.py",
        "test_tool_transcript_continuity.py",
        "test_project_learning_instruction_loop.py",
        "test_cli_focus_e2e_runner.py",
        "cli/focus/test_runner_summary.py",
        "cli/focus/test_harness_assertions.py",
        "cli/focus/harness/test_provider_session_resilience.py",
    ):
        assert f"tests/e2e/{owner}" in target


def test_live_workflow_bootstraps_manual_only_on_trusted_default_branch() -> None:
    workflow = yaml.load(
        (ROOT / ".github/workflows/focus-live-validation.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    job = workflow["jobs"]["baseline"]
    assert (
        job["if"]
        == "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"
    )
    assert job["environment"] == "focus-live-validation"
    assert job["timeout-minutes"] == "12"
    checkout = job["steps"][0]
    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert checkout["with"]["persist-credentials"] == "false"
    live = next(step for step in job["steps"] if "baseline-live" in step.get("run", ""))
    assert live["env"]["MINIMAX_API_KEY"] == "${{ secrets.MINIMAX_API_KEY }}"
    assert "secrets.MINIMAX_API_KEY" not in str(
        [step for step in job["steps"] if step is not live]
    )
    upload = job["steps"][-1]
    assert upload["if"] == "always()"
    assert upload["with"]["retention-days"] == "14"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "${{ runner.temp }}/focus-evidence/"
    assert "focus-e2e" not in workflow["jobs"]


def test_live_profile_is_explicit_bounded_and_contains_no_credentials() -> None:
    path = ROOT / "tests/e2e/fixtures/focus/minimax-baseline.json"
    config = load_config(str(path))
    provider = config.providers.openai
    assert provider.model == "MiniMax-M2.7"
    assert provider.base_url == "https://api.minimax.io/v1"
    assert provider.api_key_env == "MINIMAX_API_KEY"
    assert provider.api_key == ""
    assert provider.provider_identity["service_vendor"] == "minimax"
    assert config.security.tool_policy.max_calls_per_run == 12
    assert config.security.tool_policy.max_calls_per_tool == 6
    assert config.security.tool_policy.max_budget_cost_per_run == 24
    assert config.runtime.brain_turn_timeout_seconds == 180
    assert config.runtime.agent_loop_max_steps == 12
    assert config.agents[config.default_agent].command_policy["allow_host"] is False
    assert config.action_policy.mode == "ask"
    assert config.action_policy.default_action == "require_confirm"
    assert config.enabled_plugins == []
