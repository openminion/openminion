from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys


_ROOT = Path(__file__).resolve().parents[3]
_PYTHON = _ROOT / ".venv" / "bin" / "python3.11"


@dataclass(frozen=True)
class Suite:
    paths: tuple[str, ...]
    extra_args: tuple[str, ...] = ()
    live: bool = False
    complex: bool = False


SUITES: dict[str, Suite] = {
    "local": Suite(
        ("tests/e2e/tui/focus/test_local.py",)
    ),
    "matrix": Suite(("tests/e2e/tui/focus/test_deep_smoke_matrix.py",)),
    "adversarial-local": Suite(
        (
            "tests/e2e/tui/focus/test_deep_smoke_matrix.py",
            "tests/e2e/tui/focus/test_local.py",
            "tests/e2e/tui/focus/test_harness_artifacts.py",
            "tests/e2e/tui/focus/test_harness_assertions.py",
            "tests/cli/test_default_invocation.py",
            "tests/cli/test_focus_backend_selection.py",
            "tests/cli/presentation/test_permissions_menu.py",
            "tests/cli/interactive/test_focus_approval_persistence.py",
            "tests/cli/interactive/test_focus_turn_interrupt.py",
            "tests/cli/interactive/terminal/test_focus_input_queue.py",
            "tests/cli/interactive/terminal/test_streaming.py",
            "tests/cli/interactive/terminal/test_streaming_visuals.py",
            "tests/cli/interactive/terminal/test_pty_scrollback.py",
            "tests/cli/interactive/terminal/test_verbosity_render.py",
            "tests/cli/interactive/terminal/test_transcript.py",
            "tests/cli/interactive/terminal/test_fia_keybindings.py",
            "tests/tools/exec/test_sandbox_e2e.py",
            "tests/tools/exec/test_session_semantics.py",
            "tests/tools/exec/test_telemetry_ops.py",
            "tests/tools/test_policy_exec_approvals.py",
            "tests/tools/test_approval_pending.py",
            "tests/tools/exec/test_interfaces_contract.py",
            "tests/e2e/test_cli_chat_probe_runner.py",
        ),
    ),
    "core": Suite(("tests/e2e/tui/focus/test_live_basic.py",), live=True),
    "tools": Suite(("tests/e2e/tui/focus/test_live_tools.py",), live=True),
    "approval": Suite(
        ("tests/cli/interactive/test_focus_mode.py",),
        ("-k", "approval"),
    ),
    "research": Suite(
        ("tests/e2e/tui/focus/test_live_complex.py",),
        ("-k", "research"),
        live=True,
        complex=True,
    ),
    "coding": Suite(
        ("tests/e2e/tui/focus/test_live_complex.py",),
        ("-k", "coding"),
        live=True,
        complex=True,
    ),
    "long-running": Suite(
        ("tests/e2e/tui/focus/test_live_complex.py",),
        ("-k", "long"),
        live=True,
        complex=True,
    ),
    "soak": Suite(
        ("tests/e2e/tui/focus/test_live_soak.py",),
        live=True,
        complex=True,
    ),
    "queued-input": Suite(
        (
            "tests/cli/interactive/test_focus_input_chrome.py",
            "tests/cli/interactive/test_focus_status_line_richness.py",
        ),
        ("-k", "queued"),
    ),
    "hlpe": Suite(("tests/e2e/tui/focus/test_live_high_level_request.py",)),
    "progress-visibility": Suite(
        (
            "tests/cli/status",
            "tests/cli/interactive/test_focus_status_format_parity.py",
            "tests/cli/interactive/test_focus_status_line_richness.py",
        ),
    ),
    "tier-a": Suite(
        (
            "tests/cli/interactive/test_focus_approval_persistence.py",
            "tests/cli/interactive/test_focus_slash_commands.py",
            "tests/policy/test_policy_service.py",
            "tests/brain/test_confirmation_replay_bridge_integration.py",
            "tests/integration/test_parallel_rollout_patch_apply.py",
        ),
    ),
    "regression": Suite(
        (
            "tests/e2e/tui/focus/test_local.py",
            "tests/cli/interactive",
            "tests/cli/presentation",
            "tests/cli/status",
        ),
    ),
    "live": Suite(
        (
            "tests/e2e/tui/focus/test_live_basic.py",
            "tests/e2e/tui/focus/test_live_tools.py",
        ),
        live=True,
    ),
    "complex": Suite(
        ("tests/e2e/tui/focus/test_live_complex.py",),
        live=True,
        complex=True,
    ),
    "deep": Suite(
        (
            "tests/e2e/tui/focus/test_live_basic.py",
            "tests/e2e/tui/focus/test_live_tools.py",
            "tests/e2e/tui/focus/test_live_complex.py",
        ),
        live=True,
        complex=True,
    ),
    "all": Suite(("tests/e2e/tui/focus",), live=True, complex=True),
}


def suite_names() -> tuple[str, ...]:
    return tuple(sorted(SUITES))


def _run(
    paths: tuple[str, ...],
    *,
    env: dict[str, str],
    extra_args: tuple[str, ...] = (),
) -> int:
    command = [
        str(_PYTHON),
        "-m",
        "pytest",
        "-q",
        *paths,
        *(extra_args or []),
        "-ra",
    ]
    return subprocess.call(command, cwd=_ROOT, env=env)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = args[0] if args else "local"
    if mode in {"--list", "list"}:
        for name in suite_names():
            print(name)
        return 0
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    if mode not in SUITES:
        options = ", ".join(suite_names())
        print(f"usage: run_tui_focus_e2e.py [{options}]", file=sys.stderr)
        return 2
    suite = SUITES[mode]
    if suite.live:
        env["OPENMINION_LIVE_TUI_FOCUS_E2E"] = "1"
    if suite.complex:
        env["OPENMINION_LIVE_TUI_FOCUS_COMPLEX_E2E"] = "1"
    return _run(suite.paths, env=env, extra_args=suite.extra_args)


if __name__ == "__main__":
    raise SystemExit(main())
