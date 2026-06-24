from __future__ import annotations

import json
from argparse import Namespace

from openminion.cli.commands import tools as tools_command


def test_tools_list_json_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        tools_command,
        "_from_daemon_or_inproc",
        lambda *_args, **_kwargs: {
            "ok": True,
            "tools": [{"name": "weather", "enabled": True, "policy_allowed": True}],
        },
    )

    code = tools_command.run_tools(
        Namespace(
            tools_command="list",
            verbose=False,
            available=False,
            blocked=False,
            disabled=False,
            config=None,
        )
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "ok": True,
        "tools": [{"name": "weather", "enabled": True, "policy_allowed": True}],
    }


def test_tools_schema_json_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        tools_command,
        "_from_daemon_or_inproc",
        lambda *_args, **_kwargs: {"ok": True, "schema": {"type": "object"}},
    )

    code = tools_command.run_tools(
        Namespace(
            tools_command="schema",
            tool="weather",
            config=None,
        )
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "schema": {"type": "object"}}


def test_tools_run_json_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        tools_command,
        "_from_daemon_or_inproc",
        lambda *_args, **_kwargs: {"ok": True, "result": {"city": "Tokyo"}},
    )

    code = tools_command.run_tools(
        Namespace(
            tools_command="run",
            tool="weather",
            json_payload='{"city":"Tokyo"}',
            session="tools-session",
            config=None,
        )
    )

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "result": {"city": "Tokyo"}}
