from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from tests.e2e.cli.focus.conftest import require_live_focus
from tests.e2e.cli.focus.harness import FocusProbe, FocusScenario
from tests.e2e.cli.focus.harness.artifacts import (
    artifact_root,
    persisted_events,
    write_transcript,
)

pytestmark = [pytest.mark.e2e, pytest.mark.timeout(300)]

_MCP_FIXTURE_SERVER = (
    Path(__file__).resolve().parents[3] / "mcp" / "fixtures" / "mock_mcp_server.py"
)
_MCP_TOOL = "mcp.fixture.echo_text"


def _write_live_config(source: Path, target: Path, *, python_bin: Path) -> None:
    payload = json.loads(source.read_text(encoding="utf-8"))
    runtime = payload.setdefault("runtime", {})
    runtime["memory_enabled"] = False
    runtime["telemetry_enabled"] = True
    runtime["mcp_servers"] = [
        {
            "name": "Fixture",
            "transport": "stdio",
            "command": [str(python_bin), str(_MCP_FIXTURE_SERVER)],
            "startup_timeout_seconds": 5,
            "request_timeout_seconds": 5,
        }
    ]
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream)


def test_live_minimax_invokes_mcp_and_persists_completed_event(
    tmp_path: Path,
    python_bin: Path,
    openminion_root: Path,
    framework_root: Path,
    minimax_config_path: Path,
    minimax_agent_id: str,
) -> None:
    require_live_focus()
    if not minimax_config_path.exists():
        pytest.skip(f"missing MiniMax focus config: {minimax_config_path}")

    config_path = tmp_path / "minimax-mcp.json"
    _write_live_config(minimax_config_path, config_path, python_bin=python_bin)
    run_id = uuid4().hex
    probe = FocusProbe(
        python_bin=python_bin,
        openminion_root=openminion_root,
        framework_root=framework_root,
        data_root=artifact_root(tmp_path) / "data" / run_id,
        config_path=config_path,
        agent_id=minimax_agent_id,
        workdir=openminion_root,
        session_id=f"focus-minimax-mcp-live-{run_id}",
    )
    scenario = FocusScenario(
        scenario_id="focus-minimax-mcp-live",
        prompt=(
            "Call the mcp.fixture.echo_text tool with text "
            "'MiniMax MCP live proof', then briefly report the returned text."
        ),
        timeout=240,
    )

    with probe.session(rows=50, cols=160) as session:
        probe.wait_ready(session)
        probe.run_turn(session, scenario)
        write_transcript(
            artifact_root(tmp_path), scenario.scenario_id, session.transcript
        )

    events = persisted_events(probe)
    completed = [
        event
        for event in events
        if event.event_type == "tool.execution.completed"
        and event.data.get("tool_name") == _MCP_TOOL
        and event.data.get("status") == "succeeded"
    ]
    assert completed
    assert completed[0].turn_id
    assert any(
        event.event_type == "agent.invocation.completed"
        and event.turn_id == completed[0].turn_id
        for event in events
    )
