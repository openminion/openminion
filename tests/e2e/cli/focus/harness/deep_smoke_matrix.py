from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class DeepSmokeRow:
    scenario_id: str
    summary: str
    execution: str
    command: str
    owners: tuple[str, ...]
    covers: tuple[str, ...]
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "summary": self.summary,
            "execution": self.execution,
            "command": self.command,
            "owners": list(self.owners),
            "covers": list(self.covers),
            "evidence": list(self.evidence),
        }


REQUIRED_ITEMS: frozenset[str] = frozenset(
    {
        "permission.allow",
        "permission.deny",
        "permission.ask",
        "permission.hidden_tool",
        "permission.unavailable_tool",
        "permission.policy_blocked_tool",
        "permission.sandboxed_tool",
        "permission.unsafe_host_exec_blocked",
        "runtime.bare_openminion",
        "runtime.focus_alias",
        "runtime.openminion_run",
        "runtime.demo_stub_provider",
        "runtime.configured_provider",
        "runtime.no_provider",
        "runtime.bad_provider_config",
        "workflow.long_coding_loop",
        "workflow.repo_search_research_loop",
        "workflow.tool_execution_loop",
        "workflow.failed_tool_retry",
        "workflow.context_heavy_prompt",
        "workflow.session_continuation",
        "workflow.interrupted_session_resume",
        "workflow.delegate_flow",
        "workflow.decompose_flow",
        "workflow.plan_control",
        "workflow.mixed_research_code_loop",
        "ui.streaming_chunks",
        "ui.first_token_latency",
        "ui.long_transcript",
        "ui.copy_select",
        "ui.resize",
        "ui.progress_modes",
        "ui.verbosity_modes",
        "ui.plain_rich_default_renderer",
        "ui.non_tty_piped",
        "state.session_events",
        "state.telemetry",
        "state.artifacts",
        "state.context_traces",
        "state.memory_context",
        "state.config_data_root_isolation",
        "state.generated_file_assertion",
        "state.task_plan_projection",
        "finalization.budget_evidence_closeout",
        "finalization.iteration_cap_evidence_closeout",
        "finalization.raw_tool_payload_repair",
        "finalization.requested_label_preservation",
        "finalization.provider_fallback_recovery",
        "safety.dirty_worktree_preservation",
        "safety.read_only_write_denied",
        "break.malformed_tool_args",
        "break.huge_output",
        "break.slow_command",
        "break.command_failure",
        "break.invalid_config",
        "break.missing_env",
        "break.bad_session_id",
        "break.concurrent_sessions",
        "break.cancellation_timeout",
    }
)


MATRIX: tuple[DeepSmokeRow, ...] = (
    DeepSmokeRow(
        scenario_id="focus-surface-routing",
        summary="Canonical CLI entrypoints route to Focus/run/help without dashboard drift.",
        execution="local",
        command="PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q "
        "tests/cli/test_default_invocation.py tests/cli/test_focus_backend_selection.py -ra",
        owners=(
            "tests/cli/test_default_invocation.py",
            "tests/cli/test_focus_backend_selection.py",
        ),
        covers=(
            "runtime.bare_openminion",
            "runtime.focus_alias",
            "runtime.openminion_run",
            "runtime.demo_stub_provider",
            "runtime.no_provider",
            "runtime.bad_provider_config",
            "ui.non_tty_piped",
            "ui.plain_rich_default_renderer",
        ),
        evidence=("exit code", "captured stdout/stderr", "routed handler calls"),
    ),
    DeepSmokeRow(
        scenario_id="focus-pty-local",
        summary="Real PTY Focus starts, accepts slash/help input, submits shell escapes, "
        "resizes, and writes transcripts.",
        execution="local",
        command="PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 "
        "tests/e2e/runners/run_cli_focus_e2e.py local",
        owners=(
            "tests/e2e/cli/focus/test_local.py",
            "tests/e2e/cli/focus/harness/pty.py",
        ),
        covers=(
            "runtime.bare_openminion",
            "ui.resize",
            "ui.progress_modes",
            "state.artifacts",
            "state.config_data_root_isolation",
            "break.command_failure",
        ),
        evidence=("ansi transcript files", "visible screen snapshots", "isolated data root"),
    ),
    DeepSmokeRow(
        scenario_id="permission-approval-contracts",
        summary="Permission menus and active tool approvals preserve allow, deny, ask, "
        "session grant, and unavailable-session behavior.",
        execution="local",
        command="PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q "
        "tests/cli/presentation/test_permissions_menu.py "
        "tests/cli/interactive/test_focus_approval_persistence.py -ra",
        owners=(
            "tests/cli/presentation/test_permissions_menu.py",
            "tests/cli/interactive/test_focus_approval_persistence.py",
        ),
        covers=(
            "permission.allow",
            "permission.deny",
            "permission.ask",
            "permission.unavailable_tool",
        ),
        evidence=("permission status labels", "approval callback outcomes"),
    ),
    DeepSmokeRow(
        scenario_id="tool-exposure-policy-contracts",
        summary="Tool execution blocks hidden tools before handlers, preserves policy "
        "denials, and rejects malformed arguments.",
        execution="local",
        command="PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q "
        "tests/tools/test_policy_exec_approvals.py tests/tools/test_approval_pending.py "
        "tests/tools/exec/test_interfaces_contract.py -ra",
        owners=(
            "tests/tools/test_policy_exec_approvals.py",
            "tests/tools/test_approval_pending.py",
            "tests/tools/exec/test_interfaces_contract.py",
        ),
        covers=(
            "permission.hidden_tool",
            "permission.policy_blocked_tool",
            "break.malformed_tool_args",
        ),
        evidence=("typed tool result", "policy denial reason", "schema validation error"),
    ),
    DeepSmokeRow(
        scenario_id="exec-sandbox-breakage",
        summary="Exec tool sandbox contracts cover safe runs, unsafe host execution, "
        "slow commands, huge output, failures, and unavailable runners.",
        execution="local",
        command="PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q "
        "tests/tools/exec/test_sandbox_e2e.py tests/tools/exec/test_session_semantics.py "
        "tests/tools/exec/test_telemetry_ops.py -ra",
        owners=(
            "tests/tools/exec/test_sandbox_e2e.py",
            "tests/tools/exec/test_session_semantics.py",
            "tests/tools/exec/test_telemetry_ops.py",
        ),
        covers=(
            "permission.sandboxed_tool",
            "permission.unsafe_host_exec_blocked",
            "state.telemetry",
            "break.huge_output",
            "break.slow_command",
            "break.command_failure",
        ),
        evidence=("sandbox stdout/stderr", "timeout status", "telemetry events"),
    ),
    DeepSmokeRow(
        scenario_id="agentic-delegate-plan-contracts",
        summary="Delegate, decompose, async sub-agent, planning, and progress "
        "bridges preserve typed parent/child work breakdown state.",
        execution="local",
        command="PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q "
        "tests/brain/modes/test_delegate_e2e.py "
        "tests/brain/modes/test_decompose_e2e.py "
        "tests/brain/modes/test_delegate_integration.py "
        "tests/brain/modes/test_decompose_integration.py "
        "tests/brain/modes/test_async_delegate_unit.py "
        "tests/brain/modes/test_async_delegate_integration.py "
        "tests/brain/tool_loops/test_plan_control.py "
        "tests/brain/loop/test_plan_control_progress_bridge.py "
        "tests/tools/test_agent_delegation.py -ra",
        owners=(
            "tests/brain/modes/test_delegate_e2e.py",
            "tests/brain/modes/test_decompose_e2e.py",
            "tests/brain/modes/test_async_delegate_integration.py",
            "tests/brain/tool_loops/test_plan_control.py",
            "tests/brain/loop/test_plan_control_progress_bridge.py",
            "tests/tools/test_agent_delegation.py",
        ),
        covers=(
            "workflow.delegate_flow",
            "workflow.decompose_flow",
            "workflow.plan_control",
            "state.task_plan_projection",
        ),
        evidence=("typed delegation payloads", "decompose subtasks", "plan events"),
    ),
    DeepSmokeRow(
        scenario_id="long-loop-finalization-breakers",
        summary="Long-loop budget, cap, raw payload, requested-label, and provider "
        "fallback closeouts preserve successful tool evidence generically.",
        execution="local",
        command="PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q "
        "tests/modules/brain/loop/tools/test_engine_characterization.py "
        "tests/modules/brain/loop/tools/test_mutating_file_repetition.py "
        "-k 'FinalizeIterationCapExit or budget_exhaustion_forces_answer_only_from_prior_tool_evidence "
        "or force_finalization_rejects_raw_tool_markup or raw_tool_markup "
        "or mutating_file or tool_evidence_closeout' -ra",
        owners=(
            "tests/modules/brain/loop/tools/test_engine_characterization.py",
            "tests/modules/brain/loop/tools/test_mutating_file_repetition.py",
        ),
        covers=(
            "finalization.budget_evidence_closeout",
            "finalization.iteration_cap_evidence_closeout",
            "finalization.raw_tool_payload_repair",
            "finalization.requested_label_preservation",
            "finalization.provider_fallback_recovery",
        ),
        evidence=("termination reason", "scratchpad flags", "final evidence text"),
    ),
    DeepSmokeRow(
        scenario_id="workspace-safety-breakers",
        summary="Read-only modes, policy gates, and git recovery keep agents from "
        "claiming or overwriting unapproved workspace changes.",
        execution="local",
        command="PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q "
        "tests/brain/tools/test_readonly_gate.py "
        "tests/cli/interactive/terminal/test_fpc_readonly_mode.py "
        "tests/tools/git/test_git_recovery.py "
        "tests/tools/github/test_write_policy.py -ra",
        owners=(
            "tests/brain/tools/test_readonly_gate.py",
            "tests/cli/interactive/terminal/test_fpc_readonly_mode.py",
            "tests/tools/git/test_git_recovery.py",
            "tests/tools/github/test_write_policy.py",
        ),
        covers=(
            "safety.read_only_write_denied",
            "safety.dirty_worktree_preservation",
            "permission.policy_blocked_tool",
        ),
        evidence=("policy result", "read-only denial", "git recovery hint"),
    ),
    DeepSmokeRow(
        scenario_id="tool-fallback-provider-chain",
        summary="Provider retry, recovery pipeline, and common tool-provider chains "
        "surface fallback success without hiding policy denials.",
        execution="local",
        command="PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q "
        "tests/brain/loop/test_provider_retry_policy.py "
        "tests/brain/runtime/test_recovery_pipeline.py "
        "tests/tools/search/test_provider_chain.py "
        "tests/tools/fetch/test_plugin.py "
        "tests/tools/weather/test_plugin.py "
        "tests/tools/time/test_plugin.py -ra",
        owners=(
            "tests/brain/loop/test_provider_retry_policy.py",
            "tests/brain/runtime/test_recovery_pipeline.py",
            "tests/tools/search/test_provider_chain.py",
            "tests/tools/fetch/test_plugin.py",
            "tests/tools/weather/test_plugin.py",
            "tests/tools/time/test_plugin.py",
        ),
        covers=(
            "workflow.failed_tool_retry",
            "finalization.provider_fallback_recovery",
        ),
        evidence=("retry decision", "recovery facts", "fallback provider result"),
    ),
    DeepSmokeRow(
        scenario_id="terminal-rendering-pressure",
        summary="Terminal renderer handles streaming, latency indicators, long "
        "scrollback, copy/select behavior, verbosity, and status rendering.",
        execution="local",
        command="PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q "
        "tests/cli/interactive/terminal/test_streaming.py "
        "tests/cli/interactive/terminal/test_streaming_visuals.py "
        "tests/cli/interactive/terminal/test_pty_scrollback.py "
        "tests/cli/interactive/terminal/test_verbosity_render.py "
        "tests/cli/interactive/terminal/test_transcript.py "
        "tests/cli/interactive/terminal/test_fia_keybindings.py -ra",
        owners=(
            "tests/cli/interactive/terminal/test_streaming.py",
            "tests/cli/interactive/terminal/test_streaming_visuals.py",
            "tests/cli/interactive/terminal/test_pty_scrollback.py",
            "tests/cli/interactive/terminal/test_verbosity_render.py",
            "tests/cli/interactive/terminal/test_transcript.py",
            "tests/cli/interactive/terminal/test_fia_keybindings.py",
        ),
        covers=(
            "ui.streaming_chunks",
            "ui.first_token_latency",
            "ui.long_transcript",
            "ui.copy_select",
            "ui.verbosity_modes",
            "ui.progress_modes",
        ),
        evidence=("rendered transcript", "status line snapshots", "copy outcomes"),
    ),
    DeepSmokeRow(
        scenario_id="session-continuation-and-interrupts",
        summary="Focus and run-mode sessions keep durable events through resume, "
        "queued input, interrupts, bad IDs, and cancellation/timeouts.",
        execution="local",
        command="PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q "
        "tests/cli/test_sessions_continue.py "
        "tests/cli/interactive/test_focus_turn_interrupt.py "
        "tests/cli/interactive/terminal/test_focus_input_queue.py "
        "tests/e2e/test_cli_chat_probe_runner.py -ra",
        owners=(
            "tests/cli/test_sessions_continue.py",
            "tests/cli/interactive/test_focus_turn_interrupt.py",
            "tests/cli/interactive/terminal/test_focus_input_queue.py",
            "tests/e2e/test_cli_chat_probe_runner.py",
        ),
        covers=(
            "workflow.session_continuation",
            "workflow.interrupted_session_resume",
            "state.session_events",
            "state.context_traces",
            "break.bad_session_id",
            "break.concurrent_sessions",
            "break.cancellation_timeout",
        ),
        evidence=("session store rows", "event JSON", "transcript replay"),
    ),
    DeepSmokeRow(
        scenario_id="live-tools-policy-recovery",
        summary="Configured live provider performs tool turns, recovers after policy "
        "blocks, and persists artifacts.",
        execution="live",
        command="OPENMINION_LIVE_CLI_FOCUS_E2E=1 PYTHONDONTWRITEBYTECODE=1 "
        ".venv/bin/python3.11 tests/e2e/runners/run_cli_focus_e2e.py tools",
        owners=("tests/e2e/cli/focus/test_live_tools.py",),
        covers=(
            "runtime.configured_provider",
            "workflow.tool_execution_loop",
            "workflow.failed_tool_retry",
            "state.artifacts",
        ),
        evidence=("live transcript", "tool markers", "approval/recovery text"),
    ),
    DeepSmokeRow(
        scenario_id="live-research-and-memory",
        summary="Configured live provider performs complex research with context and "
        "memory evidence instead of a one-turn toy answer.",
        execution="live",
        command="OPENMINION_LIVE_CLI_FOCUS_E2E=1 "
        "OPENMINION_LIVE_CLI_FOCUS_COMPLEX_E2E=1 PYTHONDONTWRITEBYTECODE=1 "
        ".venv/bin/python3.11 tests/e2e/runners/run_cli_focus_e2e.py research",
        owners=(
            "tests/e2e/cli/focus/test_live_complex.py",
            "tests/e2e/test_live_skill_cli_smoke.py",
        ),
        covers=(
            "workflow.repo_search_research_loop",
            "workflow.context_heavy_prompt",
            "state.context_traces",
            "state.memory_context",
        ),
        evidence=("live transcript", "context trace JSON", "skill/memory events"),
    ),
    DeepSmokeRow(
        scenario_id="live-long-coding-soak",
        summary="Configured live provider handles long coding, file generation, "
        "snapshot transcripts, and goal-style autonomous loops.",
        execution="live",
        command="OPENMINION_LIVE_CLI_FOCUS_E2E=1 "
        "OPENMINION_LIVE_CLI_FOCUS_COMPLEX_E2E=1 PYTHONDONTWRITEBYTECODE=1 "
        ".venv/bin/python3.11 tests/e2e/runners/run_cli_focus_e2e.py soak",
        owners=(
            "tests/e2e/cli/focus/test_live_soak.py",
            "tests/e2e/cli/focus/test_live_complex.py",
        ),
        covers=(
            "workflow.long_coding_loop",
            "workflow.mixed_research_code_loop",
            "state.artifacts",
            "state.generated_file_assertion",
            "ui.long_transcript",
        ),
        evidence=("generated scratch files", "live ansi snapshots", "final transcript"),
    ),
    DeepSmokeRow(
        scenario_id="config-and-env-failure-preflight",
        summary="Config/env failure probes create preflight artifacts before live "
        "provider execution is attempted.",
        execution="local",
        command="PYTHONDONTWRITEBYTECODE=1 .venv/bin/python3.11 -m pytest -q "
        "tests/e2e/test_cli_chat_probe_runner.py -k missing_live_provider_env -ra",
        owners=("tests/e2e/test_cli_chat_probe_runner.py",),
        covers=(
            "runtime.bad_provider_config",
            "break.invalid_config",
            "break.missing_env",
        ),
        evidence=("preflight JSON", "failure reason", "artifact path"),
    ),
)


def matrix_rows() -> tuple[DeepSmokeRow, ...]:
    return MATRIX


def covered_items() -> set[str]:
    return {item for row in MATRIX for item in row.covers}


def missing_required_items() -> set[str]:
    return REQUIRED_ITEMS - covered_items()


def matrix_payload() -> dict[str, Any]:
    return {
        "schema": "openminion.tui_focus_deep_smoke.v1",
        "required_items": sorted(REQUIRED_ITEMS),
        "missing_required_items": sorted(missing_required_items()),
        "rows": [row.as_dict() for row in MATRIX],
    }


def write_matrix_artifact(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "deep-smoke-matrix.json"
    path.write_text(json.dumps(matrix_payload(), indent=2, sort_keys=True) + "\n")
    return path
