from __future__ import annotations

import pytest

from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(360)]


def test_live_focus_delegate_exact_target(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    """Prove Focus can delegate to a named configured MiniMax target."""
    require_live_focus()
    marker = "MAER exact target OK"
    target_agent = "minimax-m2-7-highspeed"
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        transcript = focus_probe.run_slash(
            session,
            f"/delegate {target_agent} Reply exactly: {marker}",
            marker="Delegation:",
        )
        write_transcript(
            artifact_root(tmp_path),
            "maer-live-delegate-exact-target",
            transcript,
        )
    assert "Delegation:" in transcript
    assert target_agent in transcript
