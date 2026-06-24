from __future__ import annotations

import json
from types import SimpleNamespace

from openminion.cli.commands.agents import agent_ls, agent_status


def test_agent_ls_json_output(capsys) -> None:
    registry = SimpleNamespace(
        list_agents=lambda: [
            SimpleNamespace(agent_id="agent-1", display_name="Agent One"),
            SimpleNamespace(agent_id="agent-2", display_name="Agent Two"),
        ],
        list_heartbeats=lambda: [
            SimpleNamespace(
                agent_id="agent-1",
                status="running",
                pid=111,
                host="127.0.0.1",
                port=8001,
            )
        ],
        is_agent_stale=lambda agent_id: agent_id == "agent-2",
    )

    code = agent_ls(registry, as_json=True)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == [
        {
            "agent_id": "agent-1",
            "display_name": "Agent One",
            "host": "127.0.0.1",
            "pid": 111,
            "port": 8001,
            "status": "running",
        },
        {
            "agent_id": "agent-2",
            "display_name": "Agent Two",
            "host": "",
            "pid": 0,
            "port": 0,
            "status": "stopped",
        },
    ]


def test_agent_status_json_output(capsys) -> None:
    heartbeat = SimpleNamespace(
        status="running",
        pid=222,
        host="127.0.0.1",
        port=9001,
        active_run_id="run-1",
        started_at="2026-06-03T00:00:00",
        last_heartbeat_at="2026-06-03T00:01:00",
    )
    registry = SimpleNamespace(
        get_agent=lambda _agent_id: SimpleNamespace(display_name="Agent One"),
        get_heartbeat=lambda _agent_id: heartbeat,
        is_agent_stale=lambda _agent_id: False,
    )

    code = agent_status(registry, "agent-1", as_json=True)

    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "agent_id": "agent-1",
        "display_name": "Agent One",
        "heartbeat": {
            "active_run_id": "run-1",
            "host": "127.0.0.1",
            "last_heartbeat_at": "2026-06-03T00:01:00",
            "pid": 222,
            "port": 9001,
            "started_at": "2026-06-03T00:00:00",
        },
        "registered": True,
        "status": "running",
    }
