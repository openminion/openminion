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
            "Plan and implement a tiny Python function under {scratch_dir}, run a "
            "focused check if available, and summarize with the exact label "
            "`result:`. Keep the change "
            "isolated to that scratch directory. Do not write JSON tool snippets "
            "in the final answer."
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
            "summarize the bug, fix, and final result using the exact label "
            "`result:`. Do not write JSON tool "
            "snippets in the final answer."
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
            "validation if available, and finish with files changed plus the "
            "exact label `result:`. "
            "Do not write JSON tool snippets in the final answer."
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
            "{scratch_dir}, build a small zero-dependency Python CLI named "
            "`loopcalc`. Keep it bounded: create at most five files total, for "
            "example `loopcalc.py`, `smoke_test.py`, `README.md`, and an "
            "optional small helper. Make at most one brief plan, then use native "
            "tool calls such as `file.write` and `file.read` to create files and "
            "verify the written content. Do not call `exec.run` in this "
            "scenario; command validation is covered by sibling Focus E2E "
            "scenarios and this one is a long-running file-persistence soak. "
            "For validation, read back at least one file and report "
            "`validation result: file-read smoke passed` if the content is "
            "present. Do not write JSON tool snippets in the final "
            "answer. Do not use plan.* tools; if you need a plan, keep it in one "
            "sentence and immediately use file.write or file.read. After the "
            "file-read validation, finish with files changed, validation "
            "result, and remaining "
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
            "design as a tiny file-based package using native tool calls such as "
            "`file.write` and `file.read`. Do not install packages and do not "
            "call `exec.run` in this soak scenario; command execution is covered "
            "by sibling Focus E2E suites. Validate by reading back at least one "
            "created file and checking that the expected content is present. "
            "Do not write JSON tool snippets in the final answer. Do not use plan.* "
            "tools; if you need a plan, keep it in one sentence and immediately "
            "use file.write or file.read. Close with a concise report using the "
            "exact labels `design:`, `implementation:`, `validation:`, and "
            "`next steps:`. For validation, write `validation: file-read smoke "
            "passed` only if the read-back content is present. Keep every file and "
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
            "README using native tool calls such as `file.write` and `file.read`. "
            "Do not install packages and do not call `exec.run` in this soak "
            "scenario; command execution is covered by sibling Focus E2E suites. "
            "Do not use shell redirection, heredocs, or JSON tool snippets in the "
            "final answer. Do not use plan.* tools; if you need a plan, keep it "
            "in one sentence and immediately use file.write or file.read. "
            "Validate by reading back at least one created file and checking that "
            "the expected content is present. Finish with the exact labels "
            "`design:`, `files:`, `validation:`, and `follow-ups:`. For "
            "validation, write `validation: file-read smoke passed` only if the "
            "read-back content is present. Keep every file and "
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
