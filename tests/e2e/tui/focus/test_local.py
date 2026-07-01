from __future__ import annotations

import pytest

from tests.e2e.tui.focus.harness import FocusProbe
from tests.e2e.tui.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.runners.run_tui_focus_e2e import suite_names

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]


def test_focus_pty_launches_and_handles_help(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        transcript = focus_probe.run_slash(session, "/help", marker="/exit")
        write_transcript(artifact_root(tmp_path), "local-help", transcript)


def test_focus_runner_exposes_tracker_suite_names() -> None:
    assert set(suite_names()) >= {
        "core",
        "tools",
        "approval",
        "research",
        "coding",
        "long-running",
        "queued-input",
        "progress-visibility",
        "regression",
        "deep",
    }
