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
            "metadata best practices. Use available search/fetch tools when useful, "
            "compare at least three points, and end with a short recommendation."
        ),
        expected_markers=("recommendation",),
        timeout=900,
    ),
    FocusScenario(
        scenario_id="research_complex_tradeoffs",
        prompt=(
            "Research terminal-agent UX patterns for long running tasks. Compare "
            "Codex-style, Claude-style, and OpenCode-style interaction patterns "
            "when evidence is available, then produce a concise tradeoff matrix "
            "and a practical recommendation for OpenMinion."
        ),
        expected_markers=("recommendation",),
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
            "Plan and implement a tiny Python function under {scratch_dir}, run a "
            "focused check if available, and summarize the result. Keep the change "
            "isolated to that scratch directory."
        ),
        expected_markers=("result",),
        timeout=900,
        requires_approval=True,
        max_auto_approvals=8,
        approval_reply="session",
    ),
    FocusScenario(
        scenario_id="coding_complex_debug_loop",
        prompt=(
            "Create a small Python module and test under {scratch_dir}. Intentionally "
            "start with a failing edge case, fix it, rerun the focused check, and "
            "summarize the bug, fix, and final result."
        ),
        expected_markers=("result",),
        timeout=1200,
        requires_approval=True,
        max_auto_approvals=8,
        approval_reply="session",
    ),
    FocusScenario(
        scenario_id="coding_long_project_slice",
        prompt=(
            "Build a tiny command-line Python project under {scratch_dir} with a "
            "module, a CLI entry file, and tests. Keep it minimal, run focused "
            "validation if available, and finish with files changed plus result."
        ),
        expected_markers=("result",),
        timeout=1500,
        requires_approval=True,
        max_auto_approvals=10,
        approval_reply="session",
    ),
)


SOAK_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    FocusScenario(
        scenario_id="goal_long_python_project_loop",
        prompt=(
            "Treat this as a long-running goal-style coding loop. Under "
            "{scratch_dir}, build a small Python package named `loopcalc` with "
            "module code, a CLI entry file, tests, and a short README. Make at "
            "most one brief plan, then use native tool calls such as `file.write` "
            "and `exec.run` to create files and run focused validation. Do not "
            "install packages; validate with commands scoped to the scratch tree "
            "such as `PYTHONPATH=. {python_bin} -m pytest` or a direct "
            "`{python_bin} -m loopcalc ...` check. Do not write JSON tool "
            "snippets in the final "
            "answer. Fix any failing edge case you discover, rerun validation, "
            "then finish with files changed, validation result, and remaining "
            "follow-ups. Keep every file and command scoped to {scratch_dir}."
        ),
        expected_markers=("validation", "files"),
        timeout=2400,
        requires_approval=True,
        max_auto_approvals=12,
        approval_reply="session",
    ),
    FocusScenario(
        scenario_id="goal_research_then_code_loop",
        prompt=(
            "Treat this as a long-running self-directed project. Under "
            "{scratch_dir}, briefly reason about a minimal design for a Python "
            "CLI that summarizes text-file word counts, then implement that "
            "design as a tiny package with tests using native tool calls such as "
            "`file.write` and `exec.run`. Do not install packages; validate with "
            "commands scoped to the scratch tree such as "
            "`PYTHONPATH=. {python_bin} -m pytest` or direct module execution. "
            "Do not write JSON tool snippets in the final answer. "
            "Run focused validation, repair any issue you "
            "find, and close with a concise report covering design, "
            "implementation, validation, and next steps. Keep every file and "
            "command scoped to {scratch_dir}."
        ),
        expected_markers=("validation", "next steps"),
        timeout=3000,
        requires_approval=True,
        max_auto_approvals=12,
        approval_reply="session",
    ),
    FocusScenario(
        scenario_id="goal_deep_research_analysis_code_loop",
        prompt=(
            "Treat this as a long-running mixed research, analysis, and coding "
            "goal. Under {scratch_dir}, first compare two or three minimal "
            "design options for a Python CLI that reads a Markdown file and "
            "prints a compact section summary report. Pick the simplest useful "
            "design, explain the tradeoff briefly, then implement it as a tiny "
            "package with module code, a CLI entry file, tests, and a short "
            "README using native tool calls such as `file.write` and `exec.run`. "
            "Do not install packages; validate only with commands scoped to "
            "{scratch_dir}, such as `PYTHONPATH=. {python_bin} -m pytest` and "
            "direct module execution. If you find a failing edge case, fix it, "
            "rerun validation, and finish with design choice, files changed, "
            "validation result, and remaining follow-ups. Keep every file and "
            "command scoped to {scratch_dir}."
        ),
        expected_markers=("design", "validation", "files"),
        timeout=3000,
        requires_approval=True,
        max_auto_approvals=12,
        approval_reply="session",
    ),
)


COMPLEX_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    *RESEARCH_LIVE_SCENARIOS,
    *CODING_LIVE_SCENARIOS,
)
