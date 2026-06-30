from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FocusScenario:
    scenario_id: str
    prompt: str
    expected_markers: tuple[str, ...] = ()
    timeout: int = 240
    requires_approval: bool = False


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
    ),
)


COMPLEX_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    *RESEARCH_LIVE_SCENARIOS,
    *CODING_LIVE_SCENARIOS,
)
