from __future__ import annotations

from dataclasses import replace

import pytest

from tests.e2e.tui.focus.conftest import require_complex_focus
from tests.e2e.tui.focus.harness import FocusProbe
from tests.e2e.tui.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.tui.focus.harness.scenarios import SOAK_LIVE_SCENARIOS

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(3600)]


@pytest.mark.parametrize(
    "scenario",
    SOAK_LIVE_SCENARIOS,
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
    scratch_dir.mkdir(parents=True)
    scenario = replace(
        scenario,
        prompt=scenario.prompt.format(
            scratch_dir=scratch_dir,
            python_bin=focus_probe.python_bin,
        ),
    )
    with focus_probe.session(rows=50, cols=160) as session:
        focus_probe.wait_ready(session)
        try:
            transcript = focus_probe.run_turn(session, scenario)
        except Exception:
            write_transcript(root, scenario.scenario_id, session.transcript)
            raise
        write_transcript(root, scenario.scenario_id, transcript)
    generated_files = [path for path in scratch_dir.rglob("*") if path.is_file()]
    assert generated_files, "soak coding scenario completed without generated files"
