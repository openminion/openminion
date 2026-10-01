from __future__ import annotations

import json
import re
import shlex
import time

import pytest

from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario, PtySession
from tests.e2e.cli.focus.harness.assertions import visible_text
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


def test_focus_pty_handles_contextual_slash_help(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    transcripts: list[str] = []
    with focus_probe.session(rows=34, cols=80) as session:
        focus_probe.wait_ready(session)
        global_help = focus_probe.run_slash(session, "/help", marker="Use /help")
        long_help = focus_probe.run_slash(
            session, "/help context-review", marker="[artifacts=<dir>]"
        )
        transcripts.extend((global_help, long_help))
        for command, marker in (
            ("/help agents", "Alias: /agent"),
            ("/help agent", "Alias: /agent"),
            ("/help /agent", "Agent selection:"),
            ("/agents ?", "/agents <agent-id-or-label>"),
            ("/agents --help", "/agents <agent-id-or-label>"),
            ("/agent --help", "List configured agents"),
            ("/help new", "/new session"),
            ("/help new session", "/help <command>"),
            ("/clear ?", "Clear chat history"),
            (
                "/review --help",
                "Run structural checks on the current or supplied diff",
            ),
            ("/exit --help", "Exit the interactive CLI"),
            ("/help statsu", "Did you mean /status?"),
            ("/status", "Status:"),
        ):
            transcripts.append(focus_probe.run_slash(session, command, marker=marker))

    output = visible_text("\n".join(transcripts))
    assert "\n               /agent)" in visible_text(global_help)
    assert "\n    [artifacts=<dir>]" in visible_text(long_help)
    assert "/agents <agent-id-or-label>" in output
    assert "Alias: /agent" in output
    assert "--profile" in output
    assert "/new session" in output
    assert "(/agents: none found)" not in output
    assert "Unknown command: /statsu" in output
    write_transcript(
        artifact_root(tmp_path),
        "local-contextual-slash-help",
        "\n".join(transcripts),
    )


def test_focus_pty_controls_durable_project(
    focus_probe: FocusProbe,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openminion.cli.commands.daemon import daemon_stop

    command = (
        "/project start --goal 'verify project controls' "
        f"--verify-command {shlex.quote(f'{focus_probe.python_bin} -c pass')}"
    )
    approval_events: list[dict[str, object]] = []
    try:
        with focus_probe.session() as session:
            focus_probe.wait_ready(session)
            launched = focus_probe.run_slash_turn(
                session,
                command,
                marker=r"Project queued:\s*awrk_[A-Za-z0-9]+",
                requires_approval=True,
                approval_events=approval_events,
            )
            assert [
                (event["action"], event["decision"]) for event in approval_events
            ] == [("project.start", "yes")]
            match = re.search(r"Project queued:\s*(awrk_[A-Za-z0-9]+)", launched)
            assert match is not None
            run_id = match.group(1)
            paused = focus_probe.run_slash_turn(
                session,
                f"/project pause {run_id}",
                marker=r"task_state: paused",
            )
            redirected = focus_probe.run_slash_turn(
                session,
                f"/project redirect {run_id} --direction 'finish the report first'",
                marker=r"direction_queued_for_next_cycle: finish the report first",
            )
            resumed = focus_probe.run_slash_turn(
                session,
                f"/project resume {run_id}",
                marker=r"task_state: active|status: running",
            )
            cancelled = focus_probe.run_slash_turn(
                session,
                f"/project cancel {run_id}",
                marker=r"cancelled",
            )
    finally:
        environment = focus_probe.environment()
        with monkeypatch.context() as context:
            for name, value in environment.items():
                context.setenv(name, value)
            daemon_stop(
                str(focus_probe.config_path),
                home_root=environment["OPENMINION_HOME"],
                data_root=environment["OPENMINION_DATA_ROOT"],
            )

    transcript = "\n".join((launched, paused, redirected, resumed, cancelled))
    assert run_id in transcript
    write_transcript(artifact_root(tmp_path), "local-project-controls", transcript)


def test_focus_pty_custom_help_is_metadata_only(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    project = tmp_path / "custom-help-project"
    commands = project / ".openminion" / "commands"
    commands.mkdir(parents=True)
    (project / "secret.txt").write_text("SECRET BODY", encoding="utf-8")
    (commands / "sample.md").write_text(
        "---\n"
        "description: Run the sample workflow\n"
        "usage: /sample <topic>\n"
        "---\n"
        "Read @secret.txt, run !`touch help-side-effect`, then use $ARGUMENTS.",
        encoding="utf-8",
    )
    (commands / "agent.md").write_text(
        "---\ndescription: custom collision\n---\ncustom body",
        encoding="utf-8",
    )
    probe = focus_probe.for_workdir(project, include_project_context=False)

    with probe.session(rows=34, cols=100) as session:
        probe.wait_ready(session)
        custom = probe.run_slash(session, "/sample --help", marker="Source: project")
        global_help = probe.run_slash(
            session, "/help", marker="Run the sample workflow"
        )
        collision = probe.run_slash(
            session, "/help agent", marker="List configured agents"
        )

    custom_output = visible_text(custom)
    assert "/sample <topic>" in custom_output
    assert "SECRET BODY" not in custom_output
    assert not (project / "help-side-effect").exists()
    assert visible_text(global_help).count("/sample") == 1
    assert "custom collision" not in visible_text(global_help)
    assert "custom collision" not in visible_text(collision)
    write_transcript(
        artifact_root(tmp_path),
        "local-custom-slash-help",
        "\n".join((custom, global_help, collision)),
    )


def test_focus_pty_renders_durable_token_report(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        transcript = focus_probe.run_slash(
            session,
            "/tokens",
            marker="Token usage",
        )
        assert "No model calls in this session yet." in transcript
        history = focus_probe.run_slash(
            session,
            "/tokens recent 3",
            marker="Token history",
        )
        assert "No model calls in the newest" in history
        telemetry = focus_probe.run_slash(
            session,
            "/telemetry",
            marker="Telemetry",
        )
        assert "No model runs in this session yet." in telemetry
        context = visible_text(
            focus_probe.run_slash(
                session,
                "/context",
                marker="Context usage:",
            )
        )
        assert "none observed in this terminal yet" in context
        assert "use /tokens for saved session totals" in context
        assert "grid" not in context
        write_transcript(artifact_root(tmp_path), "local-tokens", transcript)
        write_transcript(artifact_root(tmp_path), "local-token-history", history)
        write_transcript(artifact_root(tmp_path), "local-telemetry-empty", telemetry)
        write_transcript(artifact_root(tmp_path), "local-context-empty", context)


def test_focus_pty_inspects_telemetry_after_a_turn(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        turn = focus_probe.run_turn(
            session,
            FocusScenario(
                scenario_id="local_telemetry",
                prompt="Reply with exactly: local telemetry check",
                expected_markers=("local telemetry check",),
                timeout=60,
            ),
        )
        events = visible_text(
            focus_probe.run_slash(
                session, "/telemetry events --limit 20", marker="telemetry events:"
            )
        )
        telemetry = visible_text(
            focus_probe.run_slash(session, "/telemetry", marker="latest invocation")
        )
        tokens = visible_text(
            focus_probe.run_slash(session, "/tokens", marker="Token usage")
        )
        history = visible_text(
            focus_probe.run_slash(session, "/tokens recent 3", marker="Token history")
        )

    assert "agent.invocation.completed" in events
    assert "status: completed" in telemetry
    assert "No model calls in this session yet." not in tokens
    assert "Calls:" in tokens
    assert "sessions with model calls" in history
    write_transcript(
        artifact_root(tmp_path),
        "local-telemetry-post-turn",
        "\n".join((turn, events, telemetry, tokens, history)),
    )


def test_focus_pty_status_reports_last_turn_timing(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        turn = focus_probe.run_turn(
            session,
            FocusScenario(
                scenario_id="local_status_timing",
                prompt="Reply with exactly: timing check",
                expected_markers=("timing check",),
                timeout=60,
            ),
        )
        status = visible_text(
            focus_probe.run_slash(session, "/status", marker="Timing:")
        )

    assert "Status:" in status
    assert "Timing:" in status
    assert "Phases:" in status
    write_transcript(
        artifact_root(tmp_path),
        "local-status-timing",
        "\n".join((turn, status)),
    )


def test_focus_pty_runs_non_mutating_performance_commands(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        transcripts = [
            focus_probe.run_slash(session, "/graph help", marker="Graph viewer:"),
            focus_probe.run_slash(session, "/browser status", marker="Browser:"),
            focus_probe.run_slash(session, "/telemetry invalid", marker="usage:"),
            focus_probe.run_slash(session, "/trace invalid", marker="usage:"),
            focus_probe.run_slash(session, "/context", marker="Context usage:"),
            focus_probe.run_slash(session, "/status", marker="Status:"),
        ]

    output = visible_text("\n".join(transcripts))
    for marker in (
        "Graph viewer:",
        "Browser:",
        "usage: /telemetry",
        "usage: /trace",
        "Context usage:",
        "Status:",
    ):
        assert marker in output
    write_transcript(
        artifact_root(tmp_path),
        "local-performance-command-journey",
        "\n".join(transcripts),
    )


def test_focus_pty_handles_advertised_slash_aliases(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        focus_probe.run_slash(session, "/help", marker="Clear chat history")
        clear_offset = len(session.visible_transcript)
        session.type_line("/cls")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            screen = visible_text(session.screen_text)
            if "Clear chat history" not in screen and "Ask anything" in screen:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("/cls did not clear the visible command list")

        aliases = (
            ("/session", r"\bSession\b"),
            ("/agent", re.escape(focus_probe.agent_id)),
            ("/tool", r"\bweather\b"),
            ("/task", "Tasks"),
        )
        transcripts = [
            focus_probe.run_slash_turn(
                session,
                alias,
                marker=marker,
                timeout=15,
            )
            for alias, marker in aliases
        ]

    clear_transcript = session.visible_transcript[clear_offset:]
    assert "Unknown command:" not in clear_transcript
    assert all("Unknown command:" not in transcript for transcript in transcripts)
    assert "weather" in transcripts[2]
    write_transcript(
        artifact_root(tmp_path),
        "local-slash-aliases",
        "\n".join((clear_transcript, *transcripts)),
    )


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
        focus_probe.wait_ready(session)
        time.sleep(0.2)
        assert "Update available!" not in session.transcript
        assert "cursor position requests" not in session.transcript
        assert session.transcript.count("Ask anything") == 1

        session.type_line("/status")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            screen = session.screen_text
            if "Update available!" in screen and screen.count("Ask anything") == 1:
                break
            time.sleep(0.05)

        screen = session.screen_text
        assert "Update available!" in screen
        assert "Local source checkout detected" in screen
        assert "pip install" not in screen
        assert screen.count("Ask anything") == 1


def test_focus_runner_exposes_tracker_suite_names() -> None:
    assert set(suite_names()) >= {
        "adversarial-local",
        "core",
        "tools",
        "approval",
        "codex-first-local",
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
