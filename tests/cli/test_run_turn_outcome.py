"""Turn-outcome reporting for `openminion run`: the outcome travels in-band.

The outcome is projected into the printed envelope (metadata/JSON), the stderr footer
and the JSONL `done` frame. Process exit status is not part of this: a returned
envelope exits 0 exactly as it did in the previous release, and a transport/API error
still raises and exits 2 through the pre-existing path.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from openminion.cli.commands import run as run_mod
from openminion.cli.transport.daemon_client import DaemonStreamEvent


def _args(**overrides: Any) -> SimpleNamespace:
    base = dict(
        prompt="do the thing",
        file="",
        config=None,
        agent="",
        session="",
        purpose="",
        resume=False,
        reset_session=False,
        json=False,
        jsonl=False,
        stream=False,
        home_root=None,
        data_root=None,
        verbosity="normal",
        runtime_source="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _payload(
    *,
    brain_status: str = "done",
    respond_kind: str = "assistant",
    errors: list[dict[str, Any]] | None = None,
    confirmation_outcome: dict[str, str] | None = None,
    final_text: str = "All checks passed; task complete.",
) -> dict[str, Any]:
    metadata: dict[str, str] = {
        "brain_status": brain_status,
        "respond_kind": respond_kind,
        "finish_reason": "stop",
    }
    if confirmation_outcome is not None:
        metadata["confirmation_outcome"] = json.dumps(
            confirmation_outcome, sort_keys=True
        )
    return {
        "ok": True,
        "trace_id": "trace-1",
        "turn": {
            "final_text": final_text,
            "metadata": metadata,
            "errors": errors or [],
        },
    }


@pytest.fixture()
def run_cli(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        run_mod,
        "_load_run_config",
        lambda args: SimpleNamespace(
            agents={"openminion": SimpleNamespace()},
            runtime=SimpleNamespace(
                process_mode="single-process",
                daemon_auto_start=False,
                chat_turn_timeout_seconds=30,
            ),
        ),
    )
    monkeypatch.setattr(run_mod, "_resolve_workspace_root", lambda args, config: "")

    def _install(payload: dict[str, Any]) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def _call(*, args, auto_start, daemon_call, inproc_call):
            captured["payload"] = payload
            return SimpleNamespace(
                payload=payload, source="inproc", fallback_reason=None
            )

        monkeypatch.setattr(run_mod, "call_daemon_or_inproc", _call)
        return captured

    return _install


def test_completed_turn_reports_nothing_and_keeps_exit_zero(
    run_cli, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _payload()
    run_cli(payload)

    assert run_mod.run_openminion(_args()) == 0
    captured = capsys.readouterr()
    assert "All checks passed" in captured.out
    assert "turn_outcome" not in captured.out
    assert "turn outcome:" not in captured.err


def test_waiting_turn_reports_the_outcome_and_still_prints_the_question(
    run_cli, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _payload(
        brain_status="waiting_user",
        respond_kind="policy_confirmation_prompt",
        final_text="Policy confirmation required.\nfile.write (path=/root/x.txt)\nReply exactly yes or allow_once.",
    )
    run_cli(payload)

    assert run_mod.run_openminion(_args()) == 0
    captured = capsys.readouterr()
    assert "Policy confirmation required." in captured.out
    assert "turn outcome: needs_user" in captured.err


def test_denied_confirmation_reports_the_outcome(
    run_cli, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _payload(
        brain_status="waiting_user",
        confirmation_outcome={
            "result": "denied",
            "command_id": "cmd-7",
            "cursor": "2",
        },
        final_text="Confirmation denied. The action was not run.",
    )
    run_cli(payload)

    assert run_mod.run_openminion(_args()) == 0
    captured = capsys.readouterr()
    assert "Confirmation denied." in captured.out
    assert "turn outcome: denied" in captured.err


def test_existing_errors_take_precedence_over_waiting(
    run_cli, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _payload(
        brain_status="waiting_user",
        errors=[{"code": "PROVIDER_ERROR", "message": "boom"}],
    )
    run_cli(payload)

    assert run_mod.run_openminion(_args()) == 0
    assert "turn outcome: failed" in capsys.readouterr().err


def test_json_output_carries_the_outcome_without_the_footer(
    run_cli, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _payload(brain_status="waiting_user", final_text="Which file?")
    run_cli(payload)

    assert run_mod.run_openminion(_args(json=True)) == 0
    captured = capsys.readouterr()
    printed = json.loads(captured.out.strip())
    assert printed["turn_outcome"] == "needs_user"
    assert printed["ok"] is True
    assert "turn outcome:" not in captured.err


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_payload(), id="completed"),
        pytest.param(_payload(brain_status="waiting_user"), id="needs_user"),
        pytest.param(
            _payload(
                brain_status="waiting_user",
                confirmation_outcome={"result": "denied", "command_id": "cmd-1"},
            ),
            id="denied",
        ),
        pytest.param(
            _payload(errors=[{"code": "PROVIDER_ERROR", "message": "boom"}]),
            id="failed",
        ),
    ],
)
def test_returned_envelope_exit_status_is_unchanged_for_every_outcome(
    run_cli, payload: dict[str, Any]
) -> None:
    """Exit is 0 on the returned-envelope route whatever the reported outcome."""
    run_cli(payload)

    assert run_mod.run_openminion(_args()) == 0


def test_transport_error_path_still_raises_for_the_preexisting_exit_two(
    run_cli,
) -> None:
    """``ok`` false is a transport/API error: it raises, and ``main`` exits 2."""
    run_cli({"ok": False, "error": {"code": "UNAVAILABLE", "message": "no daemon"}})

    with pytest.raises(RuntimeError):
        run_mod.run_openminion(_args())


def _install_stream(monkeypatch: pytest.MonkeyPatch, events, envelope):
    def _stream(*, endpoint, method, path, payload, timeout_s, on_event):
        for event in events:
            if on_event is not None:
                on_event(event)
        return 200, dict(envelope)

    monkeypatch.setattr(run_mod, "daemon_stream_request", _stream)
    monkeypatch.setattr(
        run_mod,
        "_load_run_config",
        lambda args: SimpleNamespace(
            agents={"openminion": SimpleNamespace()},
            runtime=SimpleNamespace(
                process_mode="daemon",
                daemon_auto_start=False,
                chat_turn_timeout_seconds=30,
            ),
        ),
    )
    monkeypatch.setattr(
        run_mod,
        "resolve_cli_roots",
        lambda **kwargs: SimpleNamespace(
            env=SimpleNamespace(openminion_log_level="INFO")
        ),
    )
    monkeypatch.setattr(
        run_mod,
        "call_daemon_or_inproc",
        lambda *, args, auto_start, daemon_call, inproc_call: SimpleNamespace(
            payload=daemon_call(endpoint=object())[1],
            source="daemon",
            fallback_reason=None,
        ),
    )


def test_stream_callback_forwards_the_outcome_on_the_done_frame(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The server emits response then done; the forwarded done frame carries the outcome."""
    payload = _payload(
        brain_status="waiting_user",
        confirmation_outcome={"result": "denied", "command_id": "cmd-9"},
        final_text="Confirmation denied. The action was not run.",
    )
    events = [
        DaemonStreamEvent(event="response", data=payload),
        DaemonStreamEvent(event="done", data={"ok": True}),
    ]
    _install_stream(monkeypatch, events, payload)

    assert run_mod.run_openminion(_args(jsonl=True)) == 0

    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert [frame["event"] for frame in frames] == ["response", "done"]
    assert frames[0]["data"]["turn"]["final_text"].startswith("Confirmation denied")
    assert frames[1]["data"]["turn_outcome"] == "denied"


def test_stream_ordinary_done_frame_makes_no_completion_claim(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _payload()
    events = [
        DaemonStreamEvent(event="response", data=payload),
        DaemonStreamEvent(event="done", data={"ok": True}),
    ]
    _install_stream(monkeypatch, events, payload)

    assert run_mod.run_openminion(_args(jsonl=True)) == 0

    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    done = [frame for frame in frames if frame["event"] == "done"]
    assert len(done) == 1
    assert "turn_outcome" not in done[0]["data"]


def test_buffered_fallback_puts_the_outcome_on_the_response_frame(
    run_cli, capsys: pytest.CaptureFixture[str]
) -> None:
    """No stream events: the synthesized response frame carries the outcome, done does not."""
    payload = _payload(brain_status="waiting_user", final_text="Which file?")
    run_cli(payload)

    assert run_mod.run_openminion(_args(jsonl=True)) == 0
    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert [frame["event"] for frame in frames] == ["response", "done"]
    assert frames[0]["data"]["turn_outcome"] == "needs_user"
    assert "turn_outcome" not in frames[1]["data"]


def test_denied_outcome_is_read_from_the_metadata_string(
    run_cli,
) -> None:
    payload = _payload(
        brain_status="waiting_user",
        confirmation_outcome={"result": "denied", "command_id": "cmd-1"},
    )
    run_cli(payload)

    assert run_mod._turn_outcome(payload) == "denied"


def test_malformed_confirmation_metadata_is_not_a_denial(
    run_cli,
) -> None:
    payload = _payload(brain_status="waiting_user")
    payload["turn"]["metadata"]["confirmation_outcome"] = "{not json"

    assert run_mod._turn_outcome(payload) == "needs_user"


def test_returned_turn_with_errors_reports_failed_on_the_stream_route(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A returned envelope with turn errors is reported as failed; exit stays 0."""
    payload = _payload(errors=[{"code": "PROVIDER_ERROR", "message": "boom"}])
    events = [
        DaemonStreamEvent(event="response", data=payload),
        DaemonStreamEvent(event="done", data={"ok": True}),
    ]
    _install_stream(monkeypatch, events, payload)

    assert run_mod.run_openminion(_args(jsonl=True)) == 0

    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert frames[-1]["event"] == "done"
    assert frames[-1]["data"]["turn_outcome"] == "failed"


def test_returned_turn_with_errors_reports_failed_on_the_json_route(
    run_cli, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _payload(errors=[{"code": "PROVIDER_ERROR", "message": "boom"}])
    run_cli(payload)

    assert run_mod.run_openminion(_args(json=True)) == 0

    printed = json.loads(capsys.readouterr().out.strip())
    assert printed["turn_outcome"] == "failed"
    assert printed["ok"] is True


def test_stream_done_uses_the_returned_envelope_when_the_stream_lacks_metadata(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The streamed response frame is a summary; the projection needs the envelope.

    This is the case the first head got wrong: the stream summary carried no turn
    metadata, so the done frame was emitted without an outcome even though the
    projection had classified the turn as denied. Holding the done frame until the
    turn returns fixes it without touching the exit status.
    """
    payload = _payload(
        brain_status="waiting_user",
        confirmation_outcome={"result": "denied", "command_id": "cmd-11"},
        final_text="Confirmation denied. The action was not run.",
    )
    summary = {"ok": True, "trace_id": "trace-1", "runtime_source": "daemon"}
    events = [
        DaemonStreamEvent(event="response", data=summary),
        DaemonStreamEvent(event="done", data={"ok": True}),
    ]
    _install_stream(monkeypatch, events, payload)

    assert run_mod.run_openminion(_args(jsonl=True)) == 0

    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert [frame["event"] for frame in frames] == ["response", "done"]
    assert frames[-1]["data"]["turn_outcome"] == "denied"
    assert sum(1 for frame in frames if frame["event"] == "done") == 1


def test_stream_question_and_status_survive_the_held_done_frame(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _payload(
        brain_status="waiting_user",
        respond_kind="policy_confirmation_prompt",
        final_text="Policy confirmation required.",
    )
    events = [
        DaemonStreamEvent(event="response", data=payload),
        DaemonStreamEvent(event="done", data={"ok": True, "status": "waiting_user"}),
    ]
    _install_stream(monkeypatch, events, payload)

    assert run_mod.run_openminion(_args(jsonl=True)) == 0

    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    done = frames[-1]
    assert done["event"] == "done"
    assert done["data"]["status"] == "waiting_user"
    assert done["data"]["ok"] is True
    assert done["data"]["turn_outcome"] == "needs_user"
    assert "Policy confirmation required." in frames[0]["data"]["turn"]["final_text"]
