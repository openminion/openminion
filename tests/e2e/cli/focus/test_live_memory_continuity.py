from __future__ import annotations

import pytest

from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.assertions import assert_exact_reply, visible_text

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(600)]


def test_live_focus_memory_survives_distinct_session(focus_probe: FocusProbe) -> None:
    require_live_focus()
    token = "MLQC-FOCUS-CONTINUITY-7B4E"
    base_probe = focus_probe.for_workdir(
        focus_probe.workdir,
        include_project_context=False,
    )
    teach_probe = base_probe.for_session(f"{base_probe.session_id}-teach")
    teach_prompt = (
        "Use memory.write to store this in persistent agent memory with "
        'record_type="fact" and key="fact:continuity_code". Omit scope so the '
        f"active agent scope is used. The exact continuity code is {token}. "
        "After the tool succeeds, reply with exactly: stored"
    )
    with teach_probe.session() as teach_session:
        teach_probe.wait_ready(teach_session)
        teach_transcript = teach_probe.run_turn(
            teach_session,
            FocusScenario(
                scenario_id="memory_continuity_teach",
                prompt=teach_prompt,
                expected_markers=("stored",),
                timeout=240,
                include_project_context=False,
            ),
        )
        assert_exact_reply(teach_transcript, teach_prompt, "stored")
        memory_report = teach_probe.run_slash(
            teach_session,
            "/memory",
            marker="Memory:",
        )
        assert "Memory:" in visible_text(memory_report)

    recall_probe = base_probe.for_session(f"{base_probe.session_id}-recall")
    recall_prompt = (
        "What is the exact continuity code I asked you to remember? "
        "Reply with only the code."
    )
    with recall_probe.session() as recall_session:
        recall_probe.wait_ready(recall_session)
        transcript = recall_probe.run_turn(
            recall_session,
            FocusScenario(
                scenario_id="memory_continuity_recall",
                prompt=recall_prompt,
                expected_markers=(token,),
                timeout=300,
                include_project_context=False,
            ),
        )
    assert_exact_reply(transcript, recall_prompt, token)
