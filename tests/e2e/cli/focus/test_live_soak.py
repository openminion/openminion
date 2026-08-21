from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time
from typing import Callable

import pytest

from tests.e2e.cli.focus.conftest import require_complex_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.scenarios import (
    SOAK_LIVE_SCENARIOS,
    assert_scenario_contract,
)

pytestmark = [pytest.mark.e2e]


def _scenario_param(scenario):
    timeout = scenario.timeout * (scenario.max_auto_continuations + 1) + 60
    return pytest.param(scenario, marks=pytest.mark.timeout(timeout))


def _snapshot_writer(path: Path, *, min_interval: float = 5.0) -> Callable[[str], None]:
    last_write = 0.0

    def write_snapshot(transcript: str) -> None:
        nonlocal last_write
        now = time.monotonic()
        if now - last_write < min_interval:
            return
        path.write_text(transcript, encoding="utf-8")
        last_write = now

    return write_snapshot


@pytest.mark.parametrize(
    "scenario",
    [_scenario_param(scenario) for scenario in SOAK_LIVE_SCENARIOS],
    ids=[scenario.scenario_id for scenario in SOAK_LIVE_SCENARIOS],
)
def test_live_focus_soak_scenarios(
    focus_probe: FocusProbe,
    scenario,
    tmp_path,
) -> None:
    require_complex_focus()
    root = artifact_root(tmp_path)
    scratch_dir = root / "scratch" / scenario.scenario_id
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scenario = replace(
        scenario,
        prompt=scenario.prompt.format(
            scratch_dir=scratch_dir,
            python_bin=focus_probe.python_bin,
        ),
    )
    active_probe = (
        focus_probe.for_workdir(scratch_dir)
        if scenario.use_scratch_workspace
        else focus_probe
    )
    with active_probe.session(
        rows=50,
        cols=160,
        on_transcript_update=_snapshot_writer(
            root / f"{scenario.scenario_id}.live.ansi.txt"
        ),
    ) as session:
        active_probe.wait_ready(session)
        try:
            transcript = active_probe.run_turn(session, scenario)
        except BaseException:
            write_transcript(root, scenario.scenario_id, session.transcript)
            raise
        write_transcript(root, scenario.scenario_id, transcript)
    assert_scenario_contract(
        scenario,
        scratch_dir=scratch_dir,
        transcript=transcript,
        python_bin=focus_probe.python_bin,
    )
