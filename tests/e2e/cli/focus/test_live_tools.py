from __future__ import annotations

import pytest

from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.assertions import (
    assert_current_time_reply,
    assert_recorded_answer,
    current_turn_events,
    final_answer_text,
    read_focus_evidence,
)
from tests.e2e.cli.focus.harness.scenarios import TOOL_LIVE_SCENARIOS

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(360)]


@pytest.mark.parametrize(
    "scenario",
    TOOL_LIVE_SCENARIOS,
    ids=[scenario.scenario_id for scenario in TOOL_LIVE_SCENARIOS],
)
def test_live_focus_tool_scenarios(
    focus_probe: FocusProbe,
    scenario,
    tmp_path,
) -> None:
    require_live_focus()
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        if scenario.scenario_id == "time_tool":
            focus_probe.run_slash(session, "/permissions readonly", marker="readonly")
            previous_events, previous_messages, _ = read_focus_evidence(
                focus_probe.environment(), focus_probe.session_id
            )
            previous_ids = {event.event_id for event in previous_events}
            transcript = focus_probe.run_turn(session, scenario)
            all_events, messages, brain_session_id = read_focus_evidence(
                focus_probe.environment(), focus_probe.session_id
            )
            events, turn_scope = current_turn_events(all_events, previous_ids)
            assert_current_time_reply(
                transcript,
                scenario.prompt,
                events,
                session_id=brain_session_id,
                turn_scope_id=turn_scope,
            )
            assert_recorded_answer(
                messages,
                session_id=focus_probe.session_id,
                turn_scope_id=turn_scope,
                previous_ids={message.id for message in previous_messages},
                answer=final_answer_text(transcript, scenario.prompt),
            )
        else:
            transcript = focus_probe.run_turn(session, scenario)
        write_transcript(artifact_root(tmp_path), scenario.scenario_id, transcript)
