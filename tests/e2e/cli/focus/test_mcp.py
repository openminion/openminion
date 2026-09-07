from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.artifacts import persisted_events
from tests.e2e.cli.focus.harness.ollama_fixture import ollama_fixture_server

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(240)]

_MCP_FIXTURE_SERVER = (
    Path(__file__).resolve().parents[3] / "mcp" / "fixtures" / "mock_mcp_server.py"
)
_MCP_TOOL = "mcp.fixture.echo_text"


def _write_config(
    path: Path,
    *,
    base_url: str,
    python_bin: Path,
) -> None:
    path.write_text(
        json.dumps(
            {
                "default_agent": "fixture",
                "agents": {
                    "fixture": {
                        "name": "fixture",
                        "provider": "ollama",
                        "model": "qwen2.5:14b",
                    }
                },
                "providers": {
                    "ollama": {
                        "model": "qwen2.5:14b",
                        "base_url": base_url,
                    }
                },
                "runtime": {
                    "demo_mode": False,
                    "memory_enabled": False,
                    "telemetry_enabled": True,
                    "mcp_servers": [
                        {
                            "name": "Fixture",
                            "transport": "stdio",
                            "command": [str(python_bin), str(_MCP_FIXTURE_SERVER)],
                            "startup_timeout_seconds": 5,
                            "request_timeout_seconds": 5,
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )


def test_focus_invokes_mcp_and_persists_completed_tool_event(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
    framework_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = (
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "mcp-echo",
                    "function": {
                        "name": _MCP_TOOL,
                        "arguments": {"text": "focus mcp proof"},
                    },
                }
            ],
        },
        {
            "role": "assistant",
            "content": (
                "FOCUS_MCP_OK\n\n"
                '<finalization_status>{"status":"final_answer",'
                '"reasoning":"MCP fixture completed."}</finalization_status>'
            ),
        },
    )

    with ollama_fixture_server(responses) as (base_url, _requests):
        config_path = tmp_path / "mcp-focus.json"
        _write_config(config_path, base_url=base_url, python_bin=python_bin)
        monkeypatch.setenv("OLLAMA_API_KEY", "fixture-key-not-for-network-use")
        probe = FocusProbe(
            python_bin=python_bin,
            openminion_root=openminion_root,
            framework_root=framework_root,
            data_root=tmp_path / "data",
            config_path=config_path,
            agent_id="fixture",
            workdir=openminion_root,
            session_id="focus-mcp-local",
        )
        scenario = FocusScenario(
            scenario_id="focus-mcp-local",
            prompt="Call the MCP echo tool once and report completion.",
            expected_markers=("FOCUS_MCP_OK",),
            timeout=180,
        )
        with probe.session(rows=50, cols=160) as session:
            probe.wait_ready(session)
            probe.run_turn(session, scenario)

    events = persisted_events(probe)
    completed = [
        event
        for event in events
        if event.event_type == "tool.execution.completed"
        and event.data.get("tool_name") == _MCP_TOOL
    ]
    assert len(completed) == 1
    event = completed[0]
    assert event.turn_id
    assert event.data["status"] == "succeeded"
    assert any(
        item.event_type == "agent.invocation.completed"
        and item.turn_id == event.turn_id
        for item in events
    )
