from __future__ import annotations

import re
import time

import pytest

from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.service import TelemetryService
from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, PtySession
from tests.e2e.cli.focus.harness.assertions import (
    assert_exact_reply,
    assert_recorded_answer,
    assert_time_only_tools,
    current_turn_events,
    read_focus_evidence,
    visible_text,
)
from tests.e2e.cli.focus.harness.artifacts import artifact_root, write_transcript
from tests.e2e.cli.focus.harness.probe import active_turn_busy
from tests.e2e.cli.focus.harness.scenarios import BASE_LIVE_SCENARIOS

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(300)]


def test_live_focus_transcript_stays_top_down_during_minimax_turn(
    focus_probe: FocusProbe,
) -> None:
    require_live_focus()
    environment = focus_probe.environment()
    environment["TERM_PROGRAM"] = "iTerm.app"
    environment["TERM"] = "screen.xterm-256color"
    with PtySession(
        argv=(
            "/usr/bin/make",
            "run-local",
            f"PYTHON={focus_probe.python_bin}",
            "ARGS="
            + " ".join(
                (
                    "--config",
                    str(focus_probe.config_path),
                    "--profile",
                    focus_probe.agent_id,
                    "--dir",
                    str(focus_probe.workdir),
                    "--no-update-check",
                )
            ),
        ),
        cwd=focus_probe.openminion_root,
        env=environment,
        rows=53,
        cols=153,
    ) as session:
        focus_probe.wait_ready(session)
        focus_probe._submit_composer_line(session, "hi")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            rows = session.screen_lines
            if any(row.startswith("Status:") for row in rows) and "❯ hi" in rows:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(
                "MiniMax turn never exposed submitted input and active footer"
            )
        tip_row = next(
            index for index, row in enumerate(rows) if row.startswith("Tip: ")
        )
        input_row = rows.index("❯ hi")
        assert input_row == tip_row + 2, "\n".join(
            f"{index}: {row!r}" for index, row in enumerate(rows)
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            rows = session.screen_lines
            if any("Done in" in row for row in rows):
                break
            tip_row = next(
                index for index, row in enumerate(rows) if row.startswith("Tip: ")
            )
            assert rows.index("❯ hi") == tip_row + 2, "\n".join(
                f"{index}: {row!r}" for index, row in enumerate(rows)
            )
            time.sleep(0.1)
        else:
            raise AssertionError("MiniMax turn did not complete")


def _observed_call_count(token_report: str) -> int:
    match = re.search(r"Calls: (\d+) metered · (\d+) unmetered", token_report)
    if match is None:
        match = re.search(r"Calls: (\d+) observed", token_report)
        assert match is not None, token_report
        return int(match.group(1))
    return int(match.group(1)) + int(match.group(2))


def _structured_trace_path(trace_listing: str) -> str:
    rendered = "".join(line.strip() for line in trace_listing.splitlines())
    suffix = "-structured.json"
    for item in rendered.split("llm/")[1:]:
        candidate = f"llm/{item}"
        if suffix in candidate:
            return candidate[: candidate.index(suffix) + len(suffix)]
    raise AssertionError("selected invocation has no structured trace")


def test_structured_trace_path_rejoins_terminal_wrapping() -> None:
    listing = """trace files:
  llm/invocation/step01-call01-http-response.json
  llm/invocation/step01-call01-struct
ured.json
"""

    assert _structured_trace_path(listing) == (
        "llm/invocation/step01-call01-structured.json"
    )


def _seed_foreign_invocation(focus_probe: FocusProbe) -> str:
    invocation_id = "foreign-focus-telemetry-invocation"
    service = TelemetryService(env=focus_probe.environment())
    for event_type, status in (
        ("agent.invocation.started", "running"),
        ("agent.invocation.failed", "failed"),
    ):
        service.record_event_sync(
            TelemetryEvent(
                session_id="foreign-focus-session",
                turn_id="foreign-focus-turn",
                invocation_id=invocation_id,
                event_type=event_type,
                data={"status": status, "failure_code": "FOREIGN_FAILURE"},
            )
        )
    service.close_sync()
    return invocation_id


@pytest.mark.parametrize(
    "scenario",
    BASE_LIVE_SCENARIOS,
    ids=[scenario.scenario_id for scenario in BASE_LIVE_SCENARIOS],
)
def test_live_focus_basic_turn(
    focus_probe: FocusProbe,
    scenario,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    require_live_focus()
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        if scenario.scenario_id == "exact_reply":
            focus_probe.run_slash(session, "/permissions readonly", marker="readonly")
        previous_events, previous_messages, _ = read_focus_evidence(
            focus_probe.environment(), focus_probe.session_id
        )
        previous_ids = {event.event_id for event in previous_events}
        transcript = focus_probe.run_turn(session, scenario)
        if scenario.scenario_id == "exact_reply":
            assert_exact_reply(transcript, scenario.prompt, "CLI Focus live smoke OK")
            all_events, messages, brain_session_id = read_focus_evidence(
                focus_probe.environment(), focus_probe.session_id
            )
            events, turn_scope = current_turn_events(all_events, previous_ids)
            assert_time_only_tools(
                events, session_id=brain_session_id, turn_scope_id=turn_scope
            )
            assert_recorded_answer(
                messages,
                session_id=focus_probe.session_id,
                turn_scope_id=turn_scope,
                previous_ids={message.id for message in previous_messages},
                answer="CLI Focus live smoke OK",
            )
        foreign_invocation_id = _seed_foreign_invocation(focus_probe)

        telemetry = visible_text(
            focus_probe.run_slash(
                session,
                "/telemetry",
                marker="latest invocation",
            )
        )
        assert "status: completed" in telemetry
        assert foreign_invocation_id not in telemetry
        assert "next: /telemetry failed | /telemetry invocation " in telemetry
        assert "shell: telemetryctl invocation show " in telemetry

        events = visible_text(
            focus_probe.run_slash(
                session,
                "/telemetry events --limit 20",
                marker="telemetry events:",
            )
        )
        assert "agent.invocation.completed" in events
        assert foreign_invocation_id not in events
        assert scenario.prompt not in events

        failed = visible_text(
            focus_probe.run_slash(
                session,
                "/telemetry failed",
                marker="No failed model runs in this session.",
            )
        )
        assert foreign_invocation_id not in failed

        tokens = visible_text(
            focus_probe.run_slash(session, "/tokens", marker="Token usage")
        )
        assert "No model calls in this session yet." not in tokens
        assert "Tokens:" in tokens
        calls_before_help = _observed_call_count(tokens)

        token_help = visible_text(
            focus_probe.run_slash(session, "/help tokens", marker="/tokens —")
        )
        assert "/tokens recent [1..20]" in token_help
        tokens_after_help = visible_text(
            focus_probe.run_slash(session, "/tokens", marker="Token usage")
        )
        assert _observed_call_count(tokens_after_help) == calls_before_help

        history = visible_text(
            focus_probe.run_slash(
                session,
                "/tokens recent 3",
                marker="Token history",
            )
        )
        assert "sessions with model calls" in history

        trace_listing = visible_text(
            focus_probe.run_slash(session, "/trace list", marker="trace files:")
        )
        assert "trace files: none" not in trace_listing
        assert "-http-response.json" in trace_listing
        assert "-structured.json" in trace_listing
        trace_path = _structured_trace_path(trace_listing)
        trace_summary = visible_text(
            focus_probe.run_slash(
                session,
                f"/trace show {trace_path}",
                marker="shell (raw content):",
            )
        )
        assert "trace:" in trace_summary
        assert trace_path.rsplit("/", 1)[-1] in trace_summary
        assert "shell (raw content): telemetryctl trace show" in trace_summary
        assert "--raw" in trace_summary
        assert scenario.prompt not in trace_summary

        write_transcript(
            artifact_root(tmp_path),
            scenario.scenario_id,
            session.transcript,
        )


def test_live_focus_contextual_help_while_busy(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    require_live_focus()
    with focus_probe.session() as session:
        focus_probe.wait_ready(session)
        before_permissions = visible_text(
            focus_probe.run_slash(session, "/permissions", marker="permissions:")
        )
        before_mode = re.search(r"permissions: ([a-z]+)", before_permissions)
        assert before_mode is not None
        turn_offset = len(session.transcript)
        focus_probe._submit_composer_line(
            session,
            "Write twenty short numbered lines about reliable software delivery.",
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            screen = session.screen_text
            if active_turn_busy(screen) or "Type to queue while" in screen:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(
                "Focus never exposed a busy turn state\n"
                f"{visible_text(session.transcript)[-2000:]}"
            )

        help_offset = len(session.transcript)
        session.type_line("/permissions ?")
        help_transcript = session.wait_for_after(
            re.escape("Show or set the sandbox approval mode"),
            offset=help_offset,
            timeout=60,
        )
        session.wait_for_after(
            r"Done in \d+(?:m\d{2}s|s)", offset=turn_offset, timeout=300
        )
        after_permissions = visible_text(
            focus_probe.run_slash(session, "/permissions", marker="permissions:")
        )
        queue = visible_text(
            focus_probe.run_slash(session, "/queue", marker="No queued messages.")
        )

        output = visible_text(help_transcript)
        assert "/permissions <default|readonly|bypass|cycle>" in output
        assert "Queued for next turn" not in output
        assert f"permissions: {before_mode.group(1)}" in after_permissions
        assert "No queued messages." in queue
        write_transcript(
            artifact_root(tmp_path),
            "live-contextual-help-while-busy",
            session.transcript,
        )


def test_live_focus_typeahead_stays_in_inline_composer_while_busy(
    focus_probe: FocusProbe,
    tmp_path,
) -> None:
    require_live_focus()
    draft = "queue this after the current response"
    with focus_probe.session(rows=42, cols=120) as session:
        focus_probe.wait_ready(session)
        turn_offset = len(session.transcript)
        focus_probe._submit_composer_line(
            session,
            "Write forty short numbered lines about reliable terminal interfaces.",
        )
        for end, character in enumerate(draft, start=1):
            session.send(character)
            expected = f"❯ {draft[:end]}"
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                rows = session.screen_lines
                cursor_visible, cursor_row, cursor_column = session.cursor_state
                if (
                    expected.rstrip() in rows
                    and cursor_visible
                    and cursor_row == rows.index(expected.rstrip()) + 1
                    and cursor_column == len(expected) + 1
                ):
                    break
                time.sleep(0.01)
            else:
                raise AssertionError(
                    "type-ahead stopped repainting while MiniMax was working\n"
                    f"expected: {expected!r}\n{session.screen_text}"
                )

        deadline = time.monotonic() + 300
        completed = False
        while time.monotonic() < deadline:
            rows = session.screen_lines
            cursor_visible, cursor_row, cursor_column = session.cursor_state
            prompt_row = rows.index(f"❯ {draft}") if f"❯ {draft}" in rows else -1
            layout_ready = (
                prompt_row >= 0
                and rows[prompt_row + 1].startswith("◆ ")
                and cursor_visible
                and (cursor_row, cursor_column) == (prompt_row + 1, len(draft) + 3)
            )
            if re.search(
                r"Done in \d+(?:m\d{2}s|s)",
                session.transcript[turn_offset:],
            ):
                completed = True
                if layout_ready:
                    break
            time.sleep(0.05)
        assert completed, "MiniMax turn did not complete while the draft stayed open"

        session.send("\x7f" * len(draft))
        write_transcript(
            artifact_root(tmp_path),
            "live-typeahead-terminal-edge",
            session.transcript,
        )
