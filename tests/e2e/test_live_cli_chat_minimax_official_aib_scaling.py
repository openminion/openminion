from __future__ import annotations

import time

import pytest

from openminion.api.runtime import APIRuntime
from openminion.modules.brain.diagnostics.status import PhaseStatus
from tests.helpers.live_cli_chat_alibaba import (
    artifact_dir,
    framework_root,
    require_live_flag,
    runtime_home_root,
)
from tests.helpers.live_e2e_profiles import resolve_live_config_path

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(420)]


_AGENT_ID = "minimax-m2-7"


def _run_probe(*, config_basename: str, run_id: str) -> list[PhaseStatus]:
    config_path = resolve_live_config_path(config_basename, framework_root())
    if not config_path.exists():
        pytest.skip(f"missing config file: {config_path}")
    data_root = artifact_dir() / "data-roots" / run_id
    data_root.mkdir(parents=True, exist_ok=True)

    captured: list[PhaseStatus] = []

    def _capture(status: object) -> None:
        if isinstance(status, PhaseStatus):
            captured.append(status)

    rt = APIRuntime.from_config_path(
        str(config_path),
        home_root=runtime_home_root(),
        data_root=data_root,
    )
    try:
        rt.run_turn(
            payload={
                "message": (
                    "Using the time tool, tell me the current UTC time "
                    "three times in a row — call the time tool each "
                    "time rather than answering from memory. Report "
                    "each result on a separate line."
                ),
                "agent": _AGENT_ID,
                "session_id": run_id,
            },
            progress_callback=_capture,
        )
    finally:
        rt.close()
    return captured


@pytest.mark.e2e
def test_live_minimax_m2_7_aib_interactive_budget_flows_through_bridge() -> None:
    require_live_flag()
    captured = _run_probe(
        config_basename="adaptive-budget-interactive-generous.json",
        run_id=f"aib-interactive-{int(time.time())}",
    )

    assert captured, "AIB-13: progress_callback never fired"

    adaptive_limits = [
        status.llm_call_limit
        for status in captured
        if status.llm_call_limit is not None and status.llm_call_limit > 1
    ]
    if not adaptive_limits:
        pytest.skip(
            "AIB-13: simple prompt did not engage adaptive loop "
            "(only DECIDE-phase emissions captured). Re-run with a "
            "prompt that forces the adaptive loop to exercise the "
            "bridge-forwarding assertion."
        )
    max_limit = max(adaptive_limits)
    assert max_limit >= 24, (
        f"AIB-13: max adaptive-loop `llm_call_limit` in captured "
        f"stream is {max_limit}, expected >= 24 (AIB-02 default). "
        f"Pre-AIB default was 12; if the stream shows 12, the "
        f"AIB-04 bridge forwarding broke or AIB-02 rolled back.\n"
        f"observed adaptive-loop limits: {sorted(set(adaptive_limits))}"
    )


@pytest.mark.e2e
def test_live_minimax_m2_7_aib_autonomous_budget_flows_through_bridge() -> None:
    require_live_flag()
    captured = _run_probe(
        config_basename="adaptive-budget-autonomous-cron.json",
        run_id=f"aib-autonomous-{int(time.time())}",
    )

    assert captured, "AIB-14: progress_callback never fired"

    adaptive_limits = [
        status.llm_call_limit
        for status in captured
        if status.llm_call_limit is not None and status.llm_call_limit > 1
    ]
    if not adaptive_limits:
        pytest.skip(
            "AIB-14: simple prompt did not engage adaptive loop "
            "(only DECIDE-phase emissions captured). Re-run with a "
            "prompt that forces the adaptive loop to exercise the "
            "autonomous-mode bridge-forwarding assertion."
        )
    max_limit = max(adaptive_limits)
    # The autonomous-cron config sets soft_cap=48. The scaled cap
    # should be at least 48 unless decision fields boost further.
    assert max_limit >= 48, (
        f"AIB-14: max adaptive-loop `llm_call_limit` in captured "
        f"stream is {max_limit}, expected >= 48 (config soft_cap). "
        f"If the stream shows 24, the per-agent AdaptiveBudgetConfig "
        f"didn't override the default — bridge forwarding broke.\n"
        f"observed adaptive-loop limits: {sorted(set(adaptive_limits))}"
    )
