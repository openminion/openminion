from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FocusScenario:
    scenario_id: str
    prompt: str
    expected_markers: tuple[str, ...] = ()
    timeout: int = 240
    requires_approval: bool = False
    max_auto_approvals: int = 5
    approval_reply: str = "yes"
    use_scratch_workspace: bool = False
    include_project_context: bool = True


BASE_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    FocusScenario(
        scenario_id="exact_reply",
        prompt="Reply with exactly: TUI focus live smoke OK",
        expected_markers=("TUI focus live smoke OK",),
        timeout=180,
    ),
)


TOOL_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    FocusScenario(
        scenario_id="time_tool",
        prompt="Use the time tool to tell me the current UTC time in one sentence.",
        expected_markers=("UTC",),
        timeout=240,
    ),
    FocusScenario(
        scenario_id="policy_recovery",
        prompt=(
            "Check whether nasm is installed by using the allowed discovery shape "
            "`command -v nasm`, then summarize the result. Do not install anything."
        ),
        expected_markers=("nasm",),
        timeout=240,
    ),
)


RESEARCH_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    FocusScenario(
        scenario_id="research_deep_brief",
        prompt=(
            "Do a compact deep-research style pass on current Python packaging "
            "metadata best practices. Use at most three search/fetch calls total, "
            "compare at least three points from available evidence, and end with "
            "a short recommended direction."
        ),
        expected_markers=("recommendation|recommended|recommend",),
        timeout=900,
    ),
    FocusScenario(
        scenario_id="research_complex_tradeoffs",
        prompt=(
            "Research terminal-agent UX patterns for long running tasks. Compare "
            "Codex-style, Claude-style, and OpenCode-style interaction patterns "
            "when evidence is available. Use at most four search/fetch calls total, "
            "then stop searching and produce a concise tradeoff matrix plus a "
            "practical recommended direction for OpenMinion."
        ),
        expected_markers=("tradeoff", "recommendation|recommended|recommend"),
        timeout=1200,
    ),
    FocusScenario(
        scenario_id="research_long_synthesis",
        prompt=(
            "Run a long-form research synthesis on robust CLI agent test harnesses. "
            "Cover PTY testing, transcript artifacts, live-provider gating, "
            "failure classification, and maintainability. End with prioritized "
            "next steps."
        ),
        expected_markers=("next steps",),
        timeout=1500,
    ),
)


CODING_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    FocusScenario(
        scenario_id="coding_deep_scratch_feature",
        prompt=(
            "In the current directory, create a tiny Python function and one "
            "minimal check. Use file tools for files and direct exec.run commands "
            "for checks. Keep it small and finish with the exact label `result:`."
        ),
        expected_markers=("result",),
        timeout=900,
        requires_approval=True,
        max_auto_approvals=8,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
    ),
    FocusScenario(
        scenario_id="coding_complex_debug_loop",
        prompt=(
            "In the current directory, create a tiny Python module and test. "
            "Include one edge case, fix any issue you find, run a focused check, "
            "and finish with the exact label `result:` plus the bug and fix."
        ),
        expected_markers=("result",),
        timeout=1200,
        requires_approval=True,
        max_auto_approvals=8,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
    ),
    FocusScenario(
        scenario_id="coding_long_project_slice",
        prompt=(
            "In the current directory, build a tiny Python CLI project with a "
            "module, CLI entry, tests, and README. Keep it under five files, run "
            "focused validation, and finish with files changed plus the exact "
            "label `result:`."
        ),
        expected_markers=("result",),
        timeout=1500,
        requires_approval=True,
        max_auto_approvals=10,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
    ),
)


SOAK_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    FocusScenario(
        scenario_id="goal_long_python_project_loop",
        prompt=(
            "Treat this as a long-running goal-style coding loop in the current "
            "directory. Build a small zero-dependency Python CLI named "
            "`loopcalc` using at most five files. Use file.write and file.read; "
            "do not call exec.run in this soak scenario. Validate by reading back "
            "one file, then finish with files changed, validation result, and "
            "remaining follow-ups."
        ),
        expected_markers=("validation", "files"),
        timeout=2400,
        requires_approval=True,
        max_auto_approvals=12,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
    ),
    FocusScenario(
        scenario_id="goal_research_then_code_loop",
        prompt=(
            "Treat this as a long-running self-directed project in the current "
            "directory. Pick a minimal design for a Python CLI that summarizes "
            "text-file word counts, implement it with file.write/file.read, and "
            "avoid installs and exec.run. Validate by reading back one created "
            "file. Close with `design:`, `implementation:`, `validation:`, and "
            "`next steps:`."
        ),
        expected_markers=("validation", "next steps"),
        timeout=3000,
        requires_approval=True,
        max_auto_approvals=12,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
    ),
    FocusScenario(
        scenario_id="goal_deep_research_analysis_code_loop",
        prompt=(
            "Treat this as a long-running mixed research, analysis, and coding "
            "goal in the current directory. Compare two minimal designs for a "
            "Python CLI that summarizes Markdown sections, pick the simpler one, "
            "and implement a tiny package with module code, CLI entry, tests, "
            "and README using file.write/file.read. Avoid installs and exec.run. "
            "Validate by reading back one created file. Finish with `design:`, "
            "`files:`, `validation:`, and `follow-ups:`."
        ),
        expected_markers=("design", "validation", "files"),
        timeout=3000,
        requires_approval=True,
        max_auto_approvals=12,
        approval_reply="session",
        use_scratch_workspace=True,
        include_project_context=False,
    ),
)


COMPLEX_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    *RESEARCH_LIVE_SCENARIOS,
    *CODING_LIVE_SCENARIOS,
)
