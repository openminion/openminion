from __future__ import annotations

import json
import logging
from argparse import Namespace
from types import SimpleNamespace

from openminion.cli.commands import run as run_command


def _run_args(*, as_json: bool = False) -> Namespace:
    return Namespace(
        prompt="hello",
        file="",
        config=None,
        agent="",
        session="",
        purpose="",
        resume=False,
        reset_session=False,
        stream=False,
        jsonl=False,
        json=as_json,
    )


def _run_config(*, process_mode: str = "single-process") -> SimpleNamespace:
    return SimpleNamespace(
        runtime=SimpleNamespace(
            process_mode=process_mode,
            daemon_auto_start=True,
            chat_turn_timeout_seconds=300,
        ),
        gateway=SimpleNamespace(api_turn_timeout_seconds=45),
    )


def test_run_openminion_json_output_single_process(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        run_command,
        "load_config",
        lambda _cfg: _run_config(),
    )
    monkeypatch.setattr(
        run_command,
        "resolve_default_agent_id",
        lambda _cfg: "default-agent",
    )
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **_kwargs: {"run_id": "trace-123", "final_text": "done"},
    )

    code = run_command.run_openminion(_run_args(as_json=True))

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "runtime_source": "inproc",
        "trace_id": "trace-123",
        "turn": {"run_id": "trace-123", "final_text": "done"},
    }


def test_run_openminion_plain_output_prefers_final_text(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        run_command,
        "load_config",
        lambda _cfg: _run_config(),
    )
    monkeypatch.setattr(
        run_command,
        "resolve_default_agent_id",
        lambda _cfg: "default-agent",
    )
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **_kwargs: {"run_id": "trace-456", "final_text": "hello from run"},
    )

    code = run_command.run_openminion(_run_args())

    assert code == 0
    assert capsys.readouterr().out.strip() == "hello from run"


def test_run_openminion_jsonl_output_single_process(monkeypatch, capsys) -> None:
    args = _run_args()
    args.jsonl = True
    monkeypatch.setattr(
        run_command,
        "load_config",
        lambda _cfg: _run_config(),
    )
    monkeypatch.setattr(run_command, "resolve_default_agent_id", lambda _cfg: "agent")
    monkeypatch.setattr(
        run_command,
        "run_turn",
        lambda **_kwargs: {"run_id": "run-jsonl", "final_text": "done"},
    )

    assert run_command.run_openminion(args) == 0

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [event["event"] for event in events] == ["response", "done"]
    assert events[0]["data"]["turn"]["run_id"] == "run-jsonl"


def test_run_openminion_daemon_transport_outlives_configured_turn(
    monkeypatch,
    capsys,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        run_command, "load_config", lambda _cfg: _run_config(process_mode="daemon")
    )
    monkeypatch.setattr(run_command, "resolve_default_agent_id", lambda _cfg: "agent")
    monkeypatch.setattr(
        run_command,
        "daemon_request",
        lambda **kwargs: (
            calls.append(dict(kwargs))
            or (200, {"ok": True, "turn": {"final_text": "done"}})
        ),
    )

    def _call_source(**kwargs):
        _status, payload = kwargs["daemon_call"](SimpleNamespace())
        return SimpleNamespace(payload=payload, source="daemon", fallback_reason="")

    monkeypatch.setattr(run_command, "call_daemon_or_inproc", _call_source)

    assert run_command.run_openminion(_run_args()) == 0

    assert calls[0]["timeout_s"] == 305
    assert capsys.readouterr().out.strip() == "done"


def test_run_openminion_suppresses_default_info_logs_and_restores_logging(
    monkeypatch, caplog
) -> None:
    monkeypatch.setattr(
        run_command,
        "load_config",
        lambda _cfg: _run_config(),
    )
    monkeypatch.setattr(
        run_command,
        "resolve_default_agent_id",
        lambda _cfg: "default-agent",
    )

    def _run_turn(**_kwargs):
        logging.getLogger("openminion.test").info("internal setup detail")
        return {"run_id": "trace-quiet", "final_text": "quiet result"}

    monkeypatch.setattr(run_command, "run_turn", _run_turn)
    previous_disable_level = logging.root.manager.disable
    caplog.set_level(logging.INFO)

    code = run_command.run_openminion(_run_args())

    assert code == 0
    assert "internal setup detail" not in caplog.text
    assert logging.root.manager.disable == previous_disable_level


def test_run_openminion_honors_explicit_log_level(monkeypatch, caplog) -> None:
    monkeypatch.setenv("OPENMINION_LOG_LEVEL", "INFO")
    monkeypatch.setattr(
        run_command,
        "load_config",
        lambda _cfg: _run_config(),
    )
    monkeypatch.setattr(
        run_command,
        "resolve_default_agent_id",
        lambda _cfg: "default-agent",
    )

    def _run_turn(**_kwargs):
        logging.getLogger("openminion.test").info("requested setup detail")
        return {"run_id": "trace-logs", "final_text": "logged result"}

    monkeypatch.setattr(run_command, "run_turn", _run_turn)
    caplog.set_level(logging.INFO)

    code = run_command.run_openminion(_run_args())

    assert code == 0
    assert "requested setup detail" in caplog.text
