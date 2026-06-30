from __future__ import annotations

from dataclasses import replace

import pytest

from tests.e2e.tui.focus.conftest import require_complex_focus
from tests.e2e.tui.focus.harness import FocusProbe
from tests.e2e.tui.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.tui.focus.harness.scenarios import COMPLEX_LIVE_SCENARIOS

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(1200)]


@pytest.mark.parametrize(
    "scenario",
    COMPLEX_LIVE_SCENARIOS,
    ids=[scenario.scenario_id for scenario in COMPLEX_LIVE_SCENARIOS],
)
def test_live_focus_complex_scenarios(
    focus_probe: FocusProbe,
    scenario,
    tmp_path,
) -> None:
    require_complex_focus()
    scratch_dir = tmp_path / scenario.scenario_id
    scratch_dir.mkdir()
    scenario = replace(
        scenario,
        prompt=scenario.prompt.format(scratch_dir=scratch_dir),
    )
    with focus_probe.session(rows=50, cols=160) as session:
        focus_probe.wait_ready(session)
        transcript = focus_probe.run_turn(session, scenario)
        write_transcript(artifact_root(tmp_path), scenario.scenario_id, transcript)
