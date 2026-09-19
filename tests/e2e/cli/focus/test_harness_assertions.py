from __future__ import annotations

from pathlib import Path
import sys

import pytest
from pyte.screens import Char

from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.storage.runtime.session_store import MessageRecord, SessionStore
from openminion.modules.storage.runtime.sqlite import connect_database
from openminion.modules.storage.runtime.migrations import migrate_database
from openminion.modules.telemetry.service import TelemetryService

from tests.e2e.cli.focus.harness.assertions import (
    assert_expected_markers,
    assert_exact_reply,
    assert_current_time_reply,
    assert_time_only_tools,
    assert_recorded_answer,
    current_turn_events,
    read_focus_evidence,
    assert_focus_turn_completed,
    turn_output_text,
)
from tests.e2e.cli.focus.harness.probe import (
    FocusProbe,
    active_approval_visible,
    active_turn_busy,
    approval_prompt_needs_reply,
    composer_echo_probe,
    continuation_cue_present,
    focus_session_id,
    inline_approval_fingerprint,
    inline_approval_key,
    inline_approval_menu,
    latest_approval_prompt,
    latest_done_after_submission,
    latest_done_event,
    latest_terminal_failure,
    latest_turn_event,
    screen_after_submission,
    sidecar_consent_prompt_visible,
)
from tests.e2e.cli.focus.harness.pty import PtySession, bracketed_paste_payload
from tests.e2e.cli.focus.harness.scenarios import (
    FocusScenario,
    assert_scenario_contract,
)

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(
    "transcript",
    (
        "❯ Reply with exactly: OK\n● OK\nDone in 1s\n",
        "◆ Reply with exactly: OK\n● OK\nDone in 1s\n",
        "◆ Reply with exactly:\n  OK\nWorking...\n"
        "● time.now(timezone=UTC)\n  └ returned timestamp\n● OK\nDone in 1s\n",
        "❯ Reply with exactly: OK\n● O\f"
        "❯ Reply with exactly: OK\n● \x1b[32mOK\x1b[0m\nDone in 1s\n",
        "❯ Reply with exactly: OK\n● OK\nDone in 1s\f"
        "❯ Reply with exactly: OK\n● OK\nDone in 1s\n❯ Ask anything",
    ),
)
def test_exact_reply_accepts_completed_answer_and_benign_redraw(
    transcript: str,
) -> None:
    assert_exact_reply(transcript, "Reply with exactly: OK", "OK")


@pytest.mark.parametrize(
    "transcript",
    (
        "❯ Reply with exactly: OK\n● Wrong\nDone in 1s\n",
        "❯ Reply with exactly: OK\nDone in 1s\n",
        "❯ Reply with exactly: OK\n● OK, here you go.\nDone in 1s\n",
        "● OK\nDone in 1s\n❯ Reply with exactly: OK\n",
        "❯ Previous prompt\n● OK\nDone in 1s\f"
        "❯ Reply with exactly: OK\n● Wrong\nDone in 1s\n",
        "❯ Reply with exactly: OK\n● OK\nDone in 1s\f"
        "❯ Reply with exactly: OK\nWorking...\n",
        "❯ Previous prompt\n● OK\nDone in 1s\f"
        "❯ Previous prompt\n● OK\nDone in 1s\n❯ Reply with exactly: OK\n",
        "❯ Reply with exactly: OK\n● ok\nDone in 1s\n",
        "● Reply with exactly: OK\n● OK\nDone in 1s\n",
        "❯ Reply with exactly: OK\n● Here is some extra prose. ● OK\nDone in 1s\n",
    ),
)
def test_exact_reply_rejects_echo_extra_prose_and_stale_answers(
    transcript: str,
) -> None:
    with pytest.raises(AssertionError):
        assert_exact_reply(transcript, "Reply with exactly: OK", "OK")


def _time_events(
    *,
    name: str = "time.now",
    session_id: str = "current-session",
    scope: str = "current-turn",
    status: str = "success",
    completed_call_id: str = "time-call",
    timestamp: str = "2026-09-18T12:34:56.123400Z",
) -> list[TelemetryEvent]:
    return [
        TelemetryEvent(
            session_id=session_id,
            turn_id=scope,
            event_type="tool.call.requested",
            data={
                "turn_scope_id": scope,
                "call_id": "time-call",
                "canonical_name": name,
            },
        ),
        TelemetryEvent(
            session_id=session_id,
            turn_id=scope,
            event_type="tool.call.completed",
            data={
                "turn_scope_id": scope,
                "call_id": completed_call_id,
                "status": status,
                "output": {"outputs": {"utc": timestamp}},
            },
        ),
    ]


@pytest.mark.parametrize("name", ("time.now", "time.in_zone"))
@pytest.mark.parametrize(
    "answer",
    (
        "2026-09-18T12:34:56.1234+00:00",
        "2026-09-18T12:34:56,123400Z",
        "2026-09-18T12:34:56.1234000Z",
    ),
)
def test_time_reply_accepts_equivalent_utc_and_multiple_acquisitions(
    name: str, answer: str
) -> None:
    events = _time_events(name=name)
    later = _time_events(timestamp="2026-09-18T12:34:57Z")
    for event in later:
        event.data["call_id"] = "second-time-call"
    events += later
    assert_current_time_reply(
        f"❯ current time\n● {answer}\nDone in 1s",
        "current time",
        events,
        session_id="current-session",
        turn_scope_id="current-turn",
    )


@pytest.mark.parametrize(
    "events",
    (
        [],
        _time_events(name="time.convert"),
        _time_events(name="time.parse_iso"),
        _time_events(session_id="foreign-session"),
        _time_events(scope="previous-turn"),
        _time_events(status="failed"),
        _time_events(completed_call_id="foreign-call"),
        _time_events()[1:],
    ),
)
def test_time_reply_requires_successful_correlated_current_acquisition(
    events: list[TelemetryEvent],
) -> None:
    with pytest.raises(AssertionError):
        assert_current_time_reply(
            "❯ current time\n● 2026-09-18T12:34:56.1234Z\nDone in 1s",
            "current time",
            events,
            session_id="current-session",
            turn_scope_id="current-turn",
        )


@pytest.mark.parametrize(
    "answer",
    (
        "It is 2026-09-18T12:34:56.1234Z UTC.",
        "2026-09-18T12:34:56Z",
        "2026-09-18T12:34:56.1234",
        "2026-09-18T14:34:56.1234+02:00",
        "2026-09-18T12:34:56.1234001Z",
        "2026-09-18T12:34:56,1234001Z",
    ),
)
def test_time_reply_rejects_prose_rounding_and_non_utc(answer: str) -> None:
    with pytest.raises((AssertionError, ValueError)):
        assert_current_time_reply(
            f"❯ current time\n● {answer}\nDone in 1s",
            "current time",
            _time_events(),
            session_id="current-session",
            turn_scope_id="current-turn",
        )


@pytest.mark.parametrize("name", ("web.search", "file.read", "exec.run"))
def test_baseline_tools_reject_successful_non_time_execution(name: str) -> None:
    with pytest.raises(AssertionError, match="unexpected successful tool"):
        assert_time_only_tools(
            _time_events(name=name),
            session_id="current-session",
            turn_scope_id="current-turn",
        )


@pytest.mark.parametrize("answer", ("OK", "Here is extra prose.\n● OK"))
def test_recorded_assistant_output_preserves_literal_line_start_markers(
    answer: str,
) -> None:
    messages = [_outbound_message(answer)]
    transcript = f"◆ Reply with exactly: OK\n● {answer}\nDone in 1s"
    if answer == "OK":
        assert_exact_reply(transcript, "Reply with exactly: OK", "OK")
        assert_recorded_answer(
            messages,
            session_id="current-session",
            turn_scope_id="current-turn",
            previous_ids=set(),
            answer="OK",
        )
    else:
        with pytest.raises(AssertionError, match="recorded assistant output"):
            assert_exact_reply(transcript, "Reply with exactly: OK", "OK")
            assert_recorded_answer(
                messages,
                session_id="current-session",
                turn_scope_id="current-turn",
                previous_ids=set(),
                answer="OK",
            )


def test_recorded_assistant_output_requires_current_session_evidence() -> None:
    messages = [_outbound_message("OK", session_id="foreign-session")]
    with pytest.raises(AssertionError, match="not recorded"):
        assert_recorded_answer(
            messages,
            session_id="current-session",
            turn_scope_id="current-turn",
            previous_ids=set(),
            answer="OK",
        )


def _outbound_message(
    answer: str,
    *,
    session_id: str = "current-session",
    request_id: str = "current-turn",
    message_id: str = "answer-message",
) -> MessageRecord:
    return MessageRecord(
        id=message_id,
        session_id=session_id,
        conversation_id="explicit-conversation",
        thread_id="",
        attach_id="",
        role="outbound",
        body=f"minimax-m2-7: {answer}",
        metadata={"request_id": request_id},
        created_at="2026-09-18T23:00:00Z",
    )


@pytest.mark.parametrize(
    "previous_ids,request_id",
    (({"answer-message"}, "current-turn"), (set(), "old-turn")),
)
def test_recorded_assistant_output_rejects_stale_message_ids_and_requests(
    previous_ids: set[str], request_id: str
) -> None:
    with pytest.raises(AssertionError, match="not recorded"):
        assert_recorded_answer(
            [_outbound_message("OK", request_id=request_id)],
            session_id="current-session",
            turn_scope_id="current-turn",
            previous_ids=previous_ids,
            answer="OK",
        )


def test_focus_evidence_uses_runtime_messages_and_explicit_conversation_owner(
    tmp_path: Path,
) -> None:
    environment = {
        "OPENMINION_HOME": str(tmp_path / "home"),
        "OPENMINION_DATA_ROOT": str(tmp_path / "data"),
    }
    db_path = tmp_path / "data" / "state" / "openminion.db"
    migrate_database(db_path)
    connection = connect_database(db_path)
    store = SessionStore(connection)
    store.resolve_session(
        agent_id="minimax-m2-7",
        channel="console",
        target="focus",
        session_id="runtime-session",
        metadata={"conversation_id": "explicit-conversation"},
    )
    message = store.append_message(
        session_id="runtime-session",
        conversation_id="explicit-conversation",
        role="outbound",
        body="minimax-m2-7: OK",
        metadata={"request_id": "current-turn"},
    )
    connection.close()
    service = TelemetryService(env=environment)
    service.record_event_sync(
        TelemetryEvent(
            session_id="runtime-session",
            turn_id="current-turn",
            event_type="agent.invocation.started",
            event_id="start-event",
            invocation_id="current-invocation",
        )
    )
    service.record_event_sync(
        TelemetryEvent(
            session_id="runtime-session::conv:explicit-conversation",
            turn_id="assistant-turn",
            event_type="turn.assistant",
            data={"role": "assistant", "content": "OK"},
        )
    )
    service.close_sync()
    events, messages, brain_session_id = read_focus_evidence(
        environment, "runtime-session"
    )
    assert brain_session_id == "runtime-session::conv:explicit-conversation"
    assert {event.session_id for event in events} == {
        "runtime-session",
        brain_session_id,
    }
    assistant = next(event for event in events if event.event_type == "turn.assistant")
    assert "content" not in assistant.data
    assert any(item.id == message.id for item in messages)
    assert_recorded_answer(
        messages,
        session_id="runtime-session",
        turn_scope_id="current-turn",
        previous_ids=set(),
        answer="OK",
    )


def _family_time_events() -> list[TelemetryEvent]:
    events = _time_events(
        name="time",
        session_id="runtime-session::conv:explicit-conversation",
        scope="current-turn",
    )
    for event in events:
        event.turn_id = "brain-local-turn"
    for event_type, status in (
        ("tool.execution.started", "running"),
        ("tool.execution.completed", "succeeded"),
    ):
        events.append(
            TelemetryEvent(
                session_id="runtime-session::conv:explicit-conversation",
                turn_id="current-turn",
                event_type=event_type,
                data={
                    "tool_call_id": "time-call",
                    "tool_name": "time.now",
                    "status": status,
                },
            )
        )
    return events


def test_time_reply_accepts_model_family_with_correlated_runtime_leaf() -> None:
    assert_current_time_reply(
        "◆ current time\n● 2026-09-18T12:34:56.1234Z\nDone in 1s",
        "current time",
        _family_time_events(),
        session_id="runtime-session::conv:explicit-conversation",
        turn_scope_id="current-turn",
    )


@pytest.mark.parametrize(
    "mismatch",
    (
        "missing_execution",
        "conversion",
        "failed",
        "foreign_call",
        "foreign_scope",
        "foreign_session",
    ),
)
def test_time_family_rejects_unproved_or_foreign_runtime_leaf(mismatch: str) -> None:
    events = _family_time_events()
    if mismatch == "missing_execution":
        events = events[:2]
    else:
        completed = events[-1]
        if mismatch == "conversion":
            completed.data["tool_name"] = "time.convert"
        elif mismatch == "failed":
            completed.data["status"] = "failed"
        elif mismatch == "foreign_call":
            completed.data["tool_call_id"] = "foreign-call"
        elif mismatch == "foreign_scope":
            completed.turn_id = "old-turn"
        elif mismatch == "foreign_session":
            completed.session_id = "foreign-session"
    with pytest.raises(AssertionError, match="time family call"):
        assert_current_time_reply(
            "◆ current time\n● 2026-09-18T12:34:56.1234Z\nDone in 1s",
            "current time",
            events,
            session_id="runtime-session::conv:explicit-conversation",
            turn_scope_id="current-turn",
        )


@pytest.mark.parametrize("requested_name", ("time.now", "time.in_zone", "exec.run"))
def test_baseline_disclosure_control_is_restricted_to_time(requested_name: str) -> None:
    events = _time_events(name="tool.request")
    events[0].data["sanitized_normalized_arguments"] = {"name": requested_name}
    if requested_name == "exec.run":
        with pytest.raises(AssertionError, match="unexpected successful tool"):
            assert_time_only_tools(
                events, session_id="current-session", turn_scope_id="current-turn"
            )
    else:
        assert_time_only_tools(
            events, session_id="current-session", turn_scope_id="current-turn"
        )


def test_current_turn_events_exclude_previous_invocations() -> None:
    old = TelemetryEvent(
        session_id="current-session",
        turn_id="previous-turn",
        event_type="agent.invocation.started",
        event_id="old-invocation",
    )
    current = TelemetryEvent(
        session_id="current-session",
        turn_id="current-turn",
        event_type="agent.invocation.started",
        event_id="current-invocation",
    )
    assert current_turn_events([old, current], {"old-invocation"}) == (
        [current],
        "current-turn",
    )


@pytest.mark.parametrize("scopes", ((), ("first-turn", "second-turn")))
def test_current_turn_events_require_unambiguous_invocation(
    scopes: tuple[str, ...],
) -> None:
    events = [
        TelemetryEvent(
            session_id="current-session",
            turn_id=scope,
            event_type="agent.invocation.started",
            event_id=scope,
        )
        for scope in scopes
    ]
    with pytest.raises(AssertionError, match="missing or ambiguous"):
        current_turn_events(events, set())


def test_focus_session_id_uses_stable_sha256_digest(tmp_path: Path) -> None:
    session_id = focus_session_id(data_root=tmp_path, node_name="focus node")

    assert session_id.startswith("focus-e2e-focus-node-")
    digest = session_id.rsplit("-", maxsplit=1)[-1]
    assert len(digest) == 32
    assert all(character in "0123456789abcdef" for character in digest)


def test_run_turn_ignores_repeated_old_completion_after_inline_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    prompt = "build the uniquely anchored fixture"
    old_completion = (
        "Budget: exhausted\nContinue in a new turn to resume.\nDone in 1s\n"
    )

    class Session:
        state = 0

        @property
        def visible_transcript(self) -> str:
            transcripts = (
                f"{old_completion}❯ {prompt}\nApproval required: file.write\n"
                "[y]es / [N]o / [a]lways:",
                f"{old_completion}❯ {prompt}\na\nStatus: Thinking...\n",
                f"{old_completion}❯ {prompt}\na\nStatus: Thinking...\n"
                f"\f{old_completion}❯ {prompt}\nStatus: Thinking...\n",
                f"{old_completion}❯ {prompt}\na\nStatus: Thinking...\n"
                f"\f❯ {prompt}\nresult: complete\nDone in 2s\n",
            )
            return transcripts[self.state]

        @property
        def screen_text(self) -> str:
            if self.state == 0:
                return "Approval required: file.write\n[y]es / [N]o / [a]lways:"
            if self.state < 3:
                return (
                    "Status: Thinking...\n❯ Type to queue while the current turn runs"
                )
            return "result: complete\nDone in 2s\n❯ Ask anything"

    session = Session()
    submitted: list[str] = []

    def submit(_session: Session, text: str) -> str:
        submitted.append(text)
        return composer_echo_probe(text)

    def approve(_session: Session, _reply: str) -> None:
        session.state = 1

    def advance(_seconds: float) -> None:
        if 0 < session.state < 3:
            session.state += 1

    monkeypatch.setattr(FocusProbe, "_submit_composer_line", staticmethod(submit))
    monkeypatch.setattr(FocusProbe, "_submit_inline_approval", staticmethod(approve))
    monkeypatch.setattr(FocusProbe, "uses_echo_agent", lambda self: True)
    monkeypatch.setattr("tests.e2e.cli.focus.harness.probe.time.sleep", advance)
    focus_probe = FocusProbe(
        python_bin=Path(sys.executable),
        openminion_root=tmp_path,
        framework_root=tmp_path,
        data_root=tmp_path,
        config_path=tmp_path / "config.json",
        agent_id="test-agent",
        workdir=tmp_path,
        session_id="test-session",
    )

    transcript = focus_probe.run_turn(
        session,  # type: ignore[arg-type]
        FocusScenario(
            scenario_id="repeated-history",
            prompt=prompt,
            requires_approval=True,
            max_auto_approvals=1,
            max_auto_continuations=1,
        ),
    )

    assert submitted == [prompt]
    assert "result: complete" in transcript


def test_expected_markers_ignore_echoed_prompt() -> None:
    prompt = "Please end with next steps."
    transcript = f"❯ {prompt}\n● Working\nDone in 4s\n"

    with pytest.raises(AssertionError, match="next steps"):
        assert_expected_markers(transcript, prompt, ("next steps",))


def test_continuation_cue_allows_terminal_line_wrapping() -> None:
    assert continuation_cue_present(
        "[act:coding] budget exhausted. Continue in a new turn to\nresume."
    )


def test_screen_after_submission_excludes_an_older_continuation_cue() -> None:
    transcript = (
        "Continue in a new turn to resume.\n"
        "❯ continue\n"
        "The task is complete.\nDone in 2s\n"
    )

    trailing = screen_after_submission(transcript, "continue")

    assert trailing is not None
    assert not continuation_cue_present(trailing)


def test_scenario_contract_requires_expected_files_and_transcript_rules(
    tmp_path: Path,
) -> None:
    scenario = FocusScenario(
        scenario_id="contract-check",
        prompt="",
        min_generated_files=2,
        expected_file_patterns=("README*",),
        forbidden_transcript_markers=("code.repo_index(",),
        validation_commands=(("{python}", "-c", "print('ok')"),),
    )
    (tmp_path / "tool.py").write_text("print('ok')\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="expected at least 2"):
        assert_scenario_contract(
            scenario,
            scratch_dir=tmp_path,
            transcript="",
        )

    (tmp_path / "README.md").write_text("# ok\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="forbidden marker"):
        assert_scenario_contract(
            scenario,
            scratch_dir=tmp_path,
            transcript="Running code.repo_index(.)",
        )

    assert_scenario_contract(
        scenario,
        scratch_dir=tmp_path,
        transcript="Done",
        python_bin=sys.executable,
    )


def test_composer_echo_probe_uses_visible_tail_for_long_input() -> None:
    prompt = "beginning that scrolls out of view " + ("x" * 80) + " visible tail"

    probe = composer_echo_probe(prompt)

    assert probe == prompt[-48:]
    assert "beginning" not in probe


def test_screen_after_submission_allows_wrapped_trailing_punctuation() -> None:
    screen = "> finish with the exact label result\nAnalyzing request...\n"

    assert screen_after_submission(screen, "finish with the exact label result.") == (
        "\nAnalyzing request...\n"
    )


def test_screen_after_submission_excludes_stale_completion() -> None:
    screen = "Done in 22s\n> session\nAnalyzing request...\n"

    assert screen_after_submission(screen, "session") == "\nAnalyzing request...\n"


def test_screen_after_submission_includes_new_completion() -> None:
    screen = "Done in 22s\n> session\nApproved.\nDone in 4s\n"

    assert screen_after_submission(screen, "session") == ("\nApproved.\nDone in 4s\n")


def test_screen_after_submission_accepts_terminal_wrapping() -> None:
    screen = (
        "Done in 22s\n"
        "> finish with the exact label `result:` plus the bug and\n"
        "  fix.\n"
        "Analyzing request...\n"
    )

    assert (
        screen_after_submission(
            screen,
            "exact label `result:` plus the bug and fix.",
        )
        == "\nAnalyzing request...\n"
    )


def test_screen_after_submission_accepts_mid_word_terminal_wrapping() -> None:
    screen = (
        "> finish with files changed and validation resu\n"
        "  lt, and remaining follow-ups.\n"
        "Analyzing request...\n"
    )

    assert (
        screen_after_submission(
            screen,
            "files changed and validation result, and remaining follow-ups.",
        )
        == "\nAnalyzing request...\n"
    )


def test_screen_after_submission_requires_rendered_input() -> None:
    assert screen_after_submission("Done in 22s\n", "session") is None


def test_expected_markers_accept_assistant_output_only() -> None:
    prompt = "Please end with next steps."
    transcript = f"❯ {prompt}\n● Here are the next steps.\nDone in 4s\n"

    assert_expected_markers(transcript, prompt, ("next steps",))


def test_expected_markers_accept_bounded_alternatives() -> None:
    prompt = "Please recommend one path."
    transcript = f"❯ {prompt}\n● Recommended direction: keep it small.\nDone in 4s\n"

    assert_expected_markers(transcript, prompt, ("recommendation|recommended",))


def test_expected_markers_reject_failed_research_fallback() -> None:
    prompt = "Research this and finish with next steps."
    transcript = (
        f"❯ {prompt}\n"
        "● Research finished, but the final synthesis step did not produce a "
        "usable synthesized answer. Next steps: continue the task.\n"
        "Done in 5m33s\n"
    )

    with pytest.raises(AssertionError, match="usable synthesized answer"):
        assert_expected_markers(transcript, prompt, ("next steps",))


def test_expected_markers_reject_provider_error_final_answer() -> None:
    prompt = "Build and validate a project, then report result."
    transcript = (
        f"❯ {prompt}\n"
        "● [act:coding] LLM error: PROVIDER_ERROR: invalid tool transcript\n"
        "Done in 2m10s\n"
    )

    with pytest.raises(AssertionError, match="llm error"):
        assert_expected_markers(transcript, prompt, ("result",))


def test_latest_terminal_failure_finds_typed_provider_failure() -> None:
    transcript = "❯ prompt\nEMPTY_PROVIDER_RESPONSE: empty after retries\n"

    match = latest_terminal_failure(transcript, offset=0)

    assert match is not None
    assert match.group(0).startswith("EMPTY_PROVIDER_RESPONSE")


def test_expected_markers_ignore_tool_output_before_final_answer() -> None:
    prompt = "Research this and provide a recommendation."
    transcript = (
        f"❯ {prompt}\n"
        "● web.search result: recommendation from an unverified snippet\n"
        "● I could not complete the requested comparison.\n"
        "Done in 4m12s\n"
    )

    with pytest.raises(AssertionError, match="recommendation"):
        assert_expected_markers(transcript, prompt, ("recommendation",))


def test_turn_completion_rejects_unresolved_approval_prompt() -> None:
    transcript = (
        "Done in 4s\n"
        "● Policy confirmation required.\n"
        "Reply exactly yes to confirm or exactly no to cancel.\n"
    )

    with pytest.raises(AssertionError, match="Policy confirmation required"):
        assert_focus_turn_completed(transcript)


def test_turn_completion_allows_resolved_approval_prompt_history() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "Reply exactly yes to confirm or exactly no to cancel.\n"
        "● Approved.\n"
        "Done in 4s\n"
    )

    assert_focus_turn_completed(transcript)


def test_latest_turn_event_prefers_completion_over_stale_approval() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "Reply exactly yes to confirm or exactly no to cancel.\n"
        "● Approved.\n"
        "Done in 4s\n"
    )

    match = latest_turn_event(transcript, offset=0)

    assert match is not None
    assert match.group(0) == "Done in 4s"


def test_latest_done_event_finds_completion_before_stale_waiting_status() -> None:
    transcript = (
        "Policy confirmation required.\n"
        "Reply exactly yes to allow once, session to allow this tool for the session, "
        "or no to cancel.\n"
        "✓ Wrote file.write · <1s\n"
        "Done in 20s\n"
        "• 20s | Waiting for your reply...\n"
        "❯ Ask anything · @ to mention a file · / for commands\n"
    )

    match = latest_done_event(transcript, offset=0)

    assert match is not None
    assert match.group(0) == "Done in 20s"


def test_latest_done_event_excludes_completion_before_new_activity() -> None:
    transcript = (
        "Done in 20s\n"
        "● Policy confirmation required.\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
    )
    offset = transcript.index("● Policy confirmation")

    assert latest_done_event(transcript, offset=offset) is None


def test_latest_done_after_submission_ignores_completion_from_prior_redraw() -> None:
    transcript = (
        "Done in 2m11s\n"
        "continue\n"
        "Working...\n\f\n"
        "Done in 2m11s\n"
        "continue\n"
        "Running file.read(greet.py)\n"
    )

    assert latest_done_after_submission(transcript, "continue") is None

    transcript += (
        "\f\nDone in 2m11s\ncontinue\nResult: project validated.\nDone in 38s\n"
    )
    match = latest_done_after_submission(transcript, "continue")

    assert match is not None
    assert match.group(0) == "Done in 38s"


def test_latest_approval_prompt_wins_when_completion_text_follows() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "file.write (path=mini.py)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "✓ Wrote file.write · <1s\n"
        "Done in 21s\n"
    )

    match = latest_approval_prompt(transcript, offset=0)

    assert match is not None
    assert "allow" in match.group(0) or "Policy confirmation" in match.group(0)


def test_approval_prompt_still_needs_reply_when_turn_completion_follows() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "file.write (path=mini.py)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "Done in 21s\n"
    )

    assert approval_prompt_needs_reply(transcript, offset=0)


def test_approval_prompt_needs_reply_when_current_screen_is_waiting() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "file.write (path=mini.py)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
    )

    assert approval_prompt_needs_reply(transcript, offset=0)


def test_high_risk_approval_prompt_needs_reply() -> None:
    transcript = "High-risk action requires confirmation\nDone in 21s\n"

    assert approval_prompt_needs_reply(transcript, offset=0)


def test_tool_activity_does_not_resolve_an_approval_prompt() -> None:
    transcript = (
        "Policy confirmation required.\n"
        "file.write (path=tiny.py)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "✓ Wrote file.write\n"
        "Waiting for your reply...\n"
    )

    assert approval_prompt_needs_reply(transcript, offset=0)


def test_approval_prompt_still_needs_reply_after_unrelated_redraws() -> None:
    transcript = (
        "\x1b[13;2H● Policy confirmation required.\n"
        "file.write (path=mini.py)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "\x1b[18;4H✓ Wrote file.write · <1s\n"
        "\x1b[21;4HDone in 21s\n"
        "\x1b[48;4H❯ Ask anything · @ to mention a file · / for commands\n"
    )

    assert approval_prompt_needs_reply(transcript, offset=0)


def test_approval_prompt_is_not_resolved_by_tool_completion() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "file.write (path=mini.py)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "✓ Wrote file.write · <1s\n"
        "Done in 21s\n"
        "❯ Ask anything · @ to mention a file · / for commands\n"
    )

    assert approval_prompt_needs_reply(transcript, offset=0)


def test_approval_prompt_does_not_need_reply_after_reply_was_queued() -> None:
    transcript = (
        "● Policy confirmation required.\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        " > session\n"
        "▊  Queued message (1 pending).\n"
    )

    assert not approval_prompt_needs_reply(transcript, offset=0)


def test_active_approval_visible_accepts_allow_once_prompt() -> None:
    screen = (
        "● Policy confirmation required.\n"
        "file.write (path=tmp/.gitkeep)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
    )

    assert active_approval_visible(screen)


def test_active_approval_visible_accepts_pinchtab_sidecar_consent() -> None:
    screen = (
        "OpenMinion can start the PinchTab browser service locally when needed.\n"
        "This launches a background process on your machine.\n"
        "Allow auto-start for PinchTab? [y/N]:"
    )

    assert sidecar_consent_prompt_visible(screen)
    assert active_approval_visible(screen)


def test_sidecar_consent_prompt_ignores_completed_response() -> None:
    screen = "Allow auto-start for PinchTab? [y/N]: y\nsidecar started\n"

    assert not sidecar_consent_prompt_visible(screen)
    assert not active_approval_visible(screen)


@pytest.mark.parametrize(
    ("screen", "menu"),
    (
        ("[A] Allow once   [S] Session allow   [D] Deny", "legacy"),
        ("[y]es / [N]o / [a]lways:", "compact"),
    ),
)
def test_inline_approval_menu_supports_both_focus_surfaces(
    screen: str,
    menu: str,
) -> None:
    assert inline_approval_menu(screen) == menu
    assert active_approval_visible(screen)


@pytest.mark.parametrize(
    "screen",
    [
        "[y]es / [N]o / [a]lways:\n❯ Ask anything",
        "[y]es / [N]o / [a]lways:\n● file.write(example.py)",
        ('[y]es / [N]o / [a]lways: ● file.write(example.py)\n  └ {"ok": true}'),
        "[y]es / [N]o / [a]lways: a\n❯ ● file.write(example.py)",
        "[y]es / [N]o / [a]lways: a\nFIRST:a",
        ("[y]es / [N]o / [a]lways: ● Running file.write(cli.py)\na\nStatus: Working"),
        (
            "[y]es / [N]o / [a]lways: ● Running file.read(module.py)\n"
            "● file.read(module.py)\n"
            "  └ def example():\n"
            "        return 1\n"
            "a\n"
            "Status: Working"
        ),
        "[A] Allow once [S] Session allow [D] Deny\nDone in 2s",
    ],
)
def test_inline_approval_menu_ignores_historical_prompts(screen: str) -> None:
    assert inline_approval_menu(screen) is None
    assert not active_approval_visible(screen)


def test_inline_approval_menu_ignores_persistent_input_footer() -> None:
    screen = (
        "Approval required for 5 queued writes\n"
        "[y]es / [N]o / [a]lways:\n"
        "input: queue next message"
    )

    assert inline_approval_menu(screen) == "compact"
    assert active_approval_visible(screen)


def test_inline_approval_menu_accepts_active_prompt_with_bare_cursor() -> None:
    screen = "Approval required: file.write(module.py)\n[y]es / [N]o / [a]lways:\n❯"

    assert inline_approval_menu(screen) == "compact"
    assert active_approval_visible(screen)


def test_inline_approval_menu_accepts_prompt_with_same_line_status() -> None:
    screen = (
        "Approval required: file.write(test_hello.py)\n"
        "[y]es / [N]o / [a]lways: ● Running file.write(README.md)"
    )

    assert inline_approval_menu(screen) == "compact"
    assert active_approval_visible(screen)


def test_inline_approval_menu_accepts_prompt_with_interleaved_tool_output() -> None:
    screen = (
        "[y]es / [N]o / [a]lways: ● file.write(tiny_math.py)\n"
        '  └ {"ok": true}\n'
        "● Running file.write(test_tiny_math.py)\n"
        "● file.write(test_tiny_math.py)\n"
        '  └ {"ok": true}\n'
        "● Running exec.run(python -m pytest -q)"
    )

    assert inline_approval_menu(screen) == "compact"
    assert active_approval_visible(screen)


def test_inline_approval_menu_uses_latest_overlapping_prompt() -> None:
    screen = (
        "[y]es / [N]o / [a]lways: Approval required: file.write(wc.py)\n"
        "[y]es / [N]o / [a]lways:"
    )

    assert inline_approval_menu(screen) == "compact"
    assert (
        inline_approval_fingerprint(screen)
        == "compact:Approval required: file.write(wc.py)"
    )


@pytest.mark.parametrize(
    ("screen", "reply", "key"),
    (
        ("[A] Allow once [S] Session allow [D] Deny", "yes", "a"),
        ("[A] Allow once [S] Session allow [D] Deny", "session", "s"),
        ("[A] Allow once [S] Session allow [D] Deny", "no", "d"),
        ("[y]es / [N]o / [a]lways:", "yes", "yes"),
        ("[y]es / [N]o / [a]lways:", "session", "a"),
        ("[y]es / [N]o / [a]lways:", "no", "no"),
    ),
)
def test_inline_approval_key_matches_the_visible_menu(
    screen: str,
    reply: str,
    key: str,
) -> None:
    assert inline_approval_key(screen, reply) == key


def test_inline_approval_fingerprint_distinguishes_consecutive_targets() -> None:
    readme = "Approval required: file.write(README.md)\n[y]es / [N]o / [a]lways:"
    module = "Approval required: file.write(module.py)\n[y]es / [N]o / [a]lways:"

    assert inline_approval_fingerprint(readme) != inline_approval_fingerprint(module)


def test_bracketed_paste_payload_wraps_multiline_prompt() -> None:
    assert bracketed_paste_payload("line one\nline two") == (
        "\x1b[200~line one\nline two\x1b[201~"
    )


def test_compact_approval_submission_handles_consecutive_prompts(
    tmp_path: Path,
) -> None:
    script = """
import asyncio
from prompt_toolkit import PromptSession

async def main():
    session = PromptSession()
    first = await session.prompt_async(
        'Approval required: file.write(README.md)\\n[y]es / [N]o / [a]lways: '
    )
    print(f'FIRST:{first}', flush=True)
    second = await session.prompt_async(
        'Approval required: file.write(module.py)\\n[y]es / [N]o / [a]lways: '
    )
    print(f'SECOND:{second}', flush=True)

asyncio.run(main())
"""
    with PtySession(
        argv=(sys.executable, "-c", script),
        cwd=tmp_path,
        rows=20,
        cols=100,
    ) as session:
        session.wait_for_after(r"file\.write\(README\.md\)", offset=0, timeout=5)
        FocusProbe._submit_inline_approval(session, "session")
        session.wait_for_after(r"file\.write\(module\.py\)", offset=0, timeout=5)
        FocusProbe._submit_inline_approval(session, "session")
        transcript = session.wait_for_after(r"SECOND:a", offset=0, timeout=5)

    assert "FIRST:a" in transcript
    assert "FIRST:alwaysalways" not in transcript


def test_active_turn_busy_accepts_current_responding_footer() -> None:
    screen = (
        "Done in 12s\n"
        "> session\n"
        "\u25cf responding | 0s | model: openai/MiniMax-M2.7 | Esc cancel\n"
    )

    assert active_turn_busy(screen)


def test_active_turn_busy_ignores_old_progress_above_ready_composer() -> None:
    screen = (
        "\u25cf responding | 4s | model: openai/MiniMax-M2.7\n"
        + "\n".join(f"answer line {index}" for index in range(8))
        + "\n\u276f Ask anything\n"
    )

    assert not active_turn_busy(screen)


def test_active_approval_visible_ignores_waiting_status_without_prompt() -> None:
    screen = "● 19s | Waiting for your reply...\n"

    assert not active_approval_visible(screen)


def test_active_approval_visible_accepts_session_grant_copy() -> None:
    screen = (
        "file.write (path=tmp/.gitkeep)\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
    )

    assert active_approval_visible(screen)


def test_compact_inline_approval_stops_after_key_echo_without_newline() -> None:
    screen = 'Approval required: file.write("wordcount.py")\n[y]es / [N]o / [a]lways: a'

    assert inline_approval_menu(screen) is None
    assert not active_approval_visible(screen)


def test_active_approval_visible_keeps_unanswered_prompt_after_input_returns() -> None:
    screen = (
        "● Policy confirmation required.\n"
        "Reply exactly yes to confirm or exactly no to cancel.\n"
        "Done in 4s\n"
        "❯ Ask anything · @ to mention a file · / for commands\n"
    )

    assert active_approval_visible(screen)


def test_active_approval_visible_keeps_unanswered_prompt_with_boxed_composer() -> None:
    screen = (
        "● Policy confirmation required.\n"
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "Done in 4s\n"
        "❯\n"
        "▊  Ask anything · @ to mention a file · / for commands\n"
        "● responding | 2s | queued: 7 | Esc cancel\n"
    )

    assert active_approval_visible(screen)


def test_active_approval_visible_ignores_waiting_history_after_input_returns() -> None:
    screen = (
        "● 19s | Waiting for your reply...\n"
        "Done in 22s\n"
        "❯ Ask anything · @ to mention a file · / for commands\n"
    )

    assert not active_approval_visible(screen)


def test_active_approval_visible_accepts_waiting_prompt_with_visible_composer() -> None:
    screen = (
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "● 19s | Waiting for your reply...\n"
        "❯ Type approval response · session to allow this tool\n"
    )

    assert active_approval_visible(screen)


def test_active_approval_visible_keeps_unanswered_prompt_active_after_done() -> None:
    screen = (
        "Reply exactly yes to allow once, session to allow this tool for the "
        "session, or no to cancel.\n"
        "✓ Wrote file.write · <1s\n"
        "Done in 18s\n"
        "● 18s | Waiting for your reply...\n"
    )

    assert active_approval_visible(screen)


def test_turn_output_uses_prompt_boundary() -> None:
    prompt = "Generate a result."
    transcript = f"banner\n❯ {prompt}\n● Final result.\nDone in 4s\n"

    assert turn_output_text(transcript, prompt).strip().startswith("● Final result.")


def test_completed_turn_allows_debug_traceback_before_final_done() -> None:
    transcript = (
        "❯ create a module and test\n"
        "● Running exec.run(python test_module.py)\n"
        "Traceback (most recent call last):\n"
        "ZeroDivisionError: division by zero\n"
        "● Running file.write(module.py)\n"
        "● result: fixed the failing edge case.\n"
        "Done in 12s\n"
    )

    assert_focus_turn_completed(transcript)


def test_turn_output_preserves_answers_across_repeated_screen_frames() -> None:
    prompt = "Inspect nasm availability."
    transcript = (
        "old turn\n❯ Inspect nasm\n availability.\n"
        "● Running command -v nasm\n"
        "\f"
        f"old turn\n❯ {prompt}\n● nasm is available.\nDone in 4s\n"
        "\f"
        "● nasm is available.\nDone in 4s\n❯ Ask anything\n"
    )

    output = turn_output_text(transcript, prompt)

    assert "nasm is available" in output
    assert "old turn" not in output


def test_pty_screen_rendering_skips_empty_cells(tmp_path) -> None:
    session = PtySession(argv=("/bin/echo", "unused"), cwd=tmp_path, rows=1, cols=3)
    session._screen.buffer[0][0] = Char(data="A")
    session._screen.buffer[0][1] = Char(data="")
    session._screen.buffer[0][2] = Char(data="B")

    assert session._screen_display_lines() == ["AB"]


@pytest.mark.parametrize(
    ("session_env", "expected"),
    [({}, "xterm-256color"), ({"TERM": "dumb"}, "dumb")],
)
def test_pty_session_owns_default_terminal_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session_env: dict[str, str],
    expected: str,
) -> None:
    monkeypatch.setenv("TERM", "dumb")
    command = (
        sys.executable,
        "-c",
        "import os; print(os.environ['TERM'])",
    )

    with PtySession(argv=command, cwd=tmp_path, env=session_env) as session:
        transcript = session.wait_for_after(expected, offset=0, timeout=5)

    assert expected in transcript
