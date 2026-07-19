from __future__ import annotations

import pytest

from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.scenarios import BASE_LIVE_SCENARIOS

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(300)]


@pytest.mark.parametrize(
    "scenario",
    BASE_LIVE_SCENARIOS,
    ids=[scenario.scenario_id for scenario in BASE_LIVE_SCENARIOS],
)
def test_live_focus_basic_turn(
    focus_probe: FocusProbe,
    scenario,
    tmp_path,
) -> None:
    require_live_focus()
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        transcript = focus_probe.run_turn(session, scenario)
        write_transcript(artifact_root(tmp_path), scenario.scenario_id, transcript)
