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


COMPLEX_LIVE_SCENARIOS: tuple[FocusScenario, ...] = (
    FocusScenario(
        scenario_id="deep_research_brief",
        prompt=(
            "Do a compact deep-research style pass on current Python packaging "
            "metadata best practices. Use available search/fetch tools when useful, "
            "compare at least three points, and end with a short recommendation."
        ),
        expected_markers=("recommendation",),
        timeout=900,
    ),
    FocusScenario(
        scenario_id="long_coding_plan",
        prompt=(
            "Plan and implement a tiny Python function under {scratch_dir}, run a "
            "focused check if available, and summarize the result. Keep the change "
            "isolated to that scratch directory."
        ),
        expected_markers=("result",),
        timeout=900,
    ),
)
