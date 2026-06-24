from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

from openminion.cli.commands import run as run_command


def test_run_openminion_json_output_single_process(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        run_command,
        "load_config",
        lambda _cfg: SimpleNamespace(
            runtime=SimpleNamespace(
                process_mode="single-process", daemon_auto_start=True
            )
        ),
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

    code = run_command.run_openminion(
        Namespace(
            prompt="hello",
            file="",
            config=None,
            agent="",
            session="",
            purpose="",
            resume=False,
            reset_session=False,
            stream=False,
            json=True,
        )
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "trace_id": "trace-123",
        "turn": {"run_id": "trace-123", "final_text": "done"},
    }


def test_run_openminion_plain_output_prefers_final_text(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        run_command,
        "load_config",
        lambda _cfg: SimpleNamespace(
            runtime=SimpleNamespace(
                process_mode="single-process", daemon_auto_start=True
            )
        ),
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

    code = run_command.run_openminion(
        Namespace(
            prompt="hello",
            file="",
            config=None,
            agent="",
            session="",
            purpose="",
            resume=False,
            reset_session=False,
            stream=False,
            json=False,
        )
    )

    assert code == 0
    assert capsys.readouterr().out.strip() == "hello from run"
