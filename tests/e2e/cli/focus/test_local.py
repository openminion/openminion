from __future__ import annotations

import json
import re
import time

import pytest

from tests.e2e.cli.focus.harness import FocusProbe, PtySession
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.runners.run_cli_focus_e2e import suite_names

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(120)]


def test_focus_artifact_root_isolates_pytest_runs(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENMINION_CLI_FOCUS_E2E_ARTIFACT_ROOT", raising=False)

    first = artifact_root(tmp_path.parent / "run-a" / tmp_path.name)
    second = artifact_root(tmp_path.parent / "run-b" / tmp_path.name)

    assert first != second


def test_focus_pty_launches_and_handles_help(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        transcript = focus_probe.run_slash(session, "/help", marker="/exit")
        write_transcript(artifact_root(tmp_path), "local-help", transcript)


def test_focus_pty_renders_durable_token_report(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        transcript = focus_probe.run_slash(
            session,
            "/tokens",
            marker="status tokens:",
        )
        assert "no token usage events" in transcript
        write_transcript(artifact_root(tmp_path), "local-tokens", transcript)


def test_focus_pty_submits_after_composer_is_ready(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    marker = "Command not found:"
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        command = "!tsur-missing-command"
        offset = len(session.transcript)
        session.send(command)
        session.wait_for_after(re.escape(command), offset=offset, timeout=10)
        submit_offset = len(session.transcript)
        session.send("\r")
        transcript = session.wait_for_after(marker, offset=submit_offset, timeout=60)
        write_transcript(artifact_root(tmp_path), "local-submit", transcript)


def test_focus_pty_survives_resize_after_launch(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    with focus_probe.session(rows=24, cols=100) as session:
        focus_probe.wait_ready(session)
        session.resize(rows=18, cols=72)
        transcript = focus_probe.run_slash(session, "/help", marker="/exit")
        write_transcript(artifact_root(tmp_path), "local-resize-help", transcript)


def test_focus_startup_notice_preserves_single_composer(
    focus_probe: FocusProbe,
) -> None:
    (focus_probe.data_root / "update-check.json").write_text(
        json.dumps({"checked_at": time.time(), "latest_version": "99.0.0"}),
        encoding="utf-8",
    )
    command = tuple(
        part for part in focus_probe.command() if part != "--no-update-check"
    )
    environment = focus_probe.environment()

    with PtySession(
        argv=command,
        cwd=focus_probe.openminion_root,
        env=environment,
        rows=42,
        cols=140,
    ) as session:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            screen = session.screen_text
            if "Update available!" in screen and screen.count("Ask anything") == 1:
                break
            time.sleep(0.05)

        screen = session.screen_text
        assert "Update available!" in screen
        assert screen.count("Ask anything") == 1


def test_focus_runner_exposes_tracker_suite_names() -> None:
    assert set(suite_names()) >= {
        "adversarial-local",
        "core",
        "tools",
        "approval",
        "matrix",
        "onboarding",
        "onboarding-live",
        "research",
        "coding",
        "long-running",
        "queued-input",
        "progress-visibility",
        "regression",
        "deep",
    }


def test_focus_probe_can_disable_project_context(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    clean_probe = focus_probe.for_workdir(
        tmp_path,
        include_project_context=False,
    )

    assert "--no-context" in clean_probe.command()
    assert "--no-context" not in focus_probe.command()


def test_focus_probe_uses_test_scoped_session(focus_probe: FocusProbe) -> None:
    command = focus_probe.command()

    assert "focus" not in command
    session_flag = command.index("--session")
    assert command[session_flag + 1] == focus_probe.session_id
    assert focus_probe.session_id.startswith("focus-e2e-")
