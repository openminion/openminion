from __future__ import annotations

import pytest

from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.assertions import visible_text

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(360)]


def test_focus_model_selection_is_clear_live_and_resumable(
    focus_probe: FocusProbe,
) -> None:
    require_live_focus()

    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        status = focus_probe.run_slash(session, "/model", marker="API format")
        assert "MiniMax-M2.7" in status
        assert "OpenAI-compatible" in status
        assert "Config key" not in status
        switched = focus_probe.run_slash(
            session,
            "/model use 1",
            marker="saved for this session",
        )
        switched_text = visible_text(switched)
        assert "MiniMax-M2.7" in switched_text
        assert "saved for this session" in switched_text
        focus_probe.run_turn(
            session,
            FocusScenario(
                scenario_id="model-ux-live",
                prompt="Reply with exactly: MODEL_UX_LIVE_OK",
                expected_markers=("MODEL_UX_LIVE_OK",),
                timeout=240,
            ),
        )

    with focus_probe.session() as resumed:
        focus_probe.wait_ready(resumed)
        status = focus_probe.run_slash(resumed, "/model", marker="API format")
        assert "current model: MiniMax-M2.7" in status
        assert "MiniMax" in status
