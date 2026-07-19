from __future__ import annotations

from dataclasses import replace

import pytest

from tests.e2e.cli.focus.conftest import require_complex_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.scenarios import COMPLEX_LIVE_SCENARIOS

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
    root = artifact_root(tmp_path)
    scratch_dir = root / "scratch" / scenario.scenario_id
    scratch_dir.mkdir(parents=True, exist_ok=True)
    scenario = replace(
        scenario,
        prompt=scenario.prompt.format(scratch_dir=scratch_dir),
    )
    active_probe = (
        focus_probe.for_workdir(
            scratch_dir,
            include_project_context=scenario.include_project_context,
        )
        if scenario.use_scratch_workspace
        else focus_probe
    )
    with active_probe.session(rows=50, cols=160) as session:
        active_probe.wait_ready(session)
        try:
            transcript = active_probe.run_turn(session, scenario)
        except Exception:
            write_transcript(root, scenario.scenario_id, session.transcript)
            raise
        write_transcript(root, scenario.scenario_id, transcript)
    if scenario.use_scratch_workspace:
        generated_files = [path for path in scratch_dir.rglob("*") if path.is_file()]
        assert generated_files, (
            f"{scenario.scenario_id} did not create files under {scratch_dir}"
        )
