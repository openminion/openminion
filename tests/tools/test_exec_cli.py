from __future__ import annotations

import json
from datetime import datetime, timezone

from typer.testing import CliRunner

from openminion.modules.tool.cli import app
from openminion.modules.tool.contracts.schemas import ResultEnvelope, WorkspaceInfo
from openminion.tools.exec.cli import app as standalone_exec_app


def _ok_envelope(tool: str, data: dict) -> ResultEnvelope:
    now = datetime.now(timezone.utc).isoformat()
    return ResultEnvelope(
        ok=True,
        tool=tool,
        run_id="run-1",
        request_id="req-1",
        policy_scope="WRITE_SAFE",
        started_at=now,
        ended_at=now,
        duration_ms=1,
        workspace=WorkspaceInfo(root=".", relative_root="."),
        data=data,
    )


def test_exec_run_routes_to_exec_tool(monkeypatch, workspace_fixture):
    _workspace, policy_path = workspace_fixture
    runner = CliRunner()
    captured = {}

    def _fake_invoke(**kwargs):
        captured.update(kwargs)
        return _ok_envelope(
            "exec.run", {"status": "running", "session_id": "execproc_1"}
        ), 0

    monkeypatch.setattr("openminion.modules.tool.cli._invoke_exec_tool", _fake_invoke)

    result = runner.invoke(
        app,
        [
            "exec",
            "run",
            "echo hi",
            "--env",
            "FOO=bar",
            "--policy",
            str(policy_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["tool"] == "exec.run"
    assert captured["tool"] == "exec.run"
    assert captured["args"]["env"] == {"FOO": "bar"}
    assert captured["args"]["command"] == "echo hi"


def test_exec_run_no_json_outputs_line_summary(monkeypatch, workspace_fixture):
    _workspace, policy_path = workspace_fixture
    runner = CliRunner()

    def _fake_invoke(**kwargs):
        return _ok_envelope(
            "exec.run", {"status": "running", "session_id": "execproc_1"}
        ), 0

    monkeypatch.setattr("openminion.modules.tool.cli._invoke_exec_tool", _fake_invoke)

    result = runner.invoke(
        app,
        [
            "exec",
            "run",
            "echo hi",
            "--policy",
            str(policy_path),
            "--no-json",
        ],
    )

    assert result.exit_code == 0
    assert not result.stdout.lstrip().startswith("{")
    assert "tool: exec.run" in result.stdout
    assert "ok: true" in result.stdout
    assert "session_id: execproc_1" in result.stdout


def test_standalone_exec_cli_no_json_outputs_line_summary(
    monkeypatch, workspace_fixture
):
    _workspace, policy_path = workspace_fixture
    runner = CliRunner()

    def _fake_execute_call_payload(**kwargs):
        return _ok_envelope(
            "exec.run", {"status": "running", "session_id": "execproc_2"}
        ), 0

    monkeypatch.setattr(
        "openminion.tools.exec.cli._execute_call_payload",
        _fake_execute_call_payload,
    )

    result = runner.invoke(
        standalone_exec_app,
        [
            "run",
            "echo hi",
            "--policy",
            str(policy_path),
            "--no-json",
        ],
    )

    assert result.exit_code == 0
    assert not result.stdout.lstrip().startswith("{")
    assert "tool: exec.run" in result.stdout
    assert "ok: true" in result.stdout
    assert "session_id: execproc_2" in result.stdout


def test_exec_poll_routes_to_process_poll(monkeypatch, workspace_fixture):
    _workspace, policy_path = workspace_fixture
    runner = CliRunner()
    captured = {}

    def _fake_invoke(**kwargs):
        captured.update(kwargs)
        return _ok_envelope("exec.poll", {"status": "running"}), 0

    monkeypatch.setattr("openminion.modules.tool.cli._invoke_exec_tool", _fake_invoke)

    result = runner.invoke(
        app,
        [
            "exec",
            "poll",
            "execproc_abcd",
            "--tail-lines",
            "150",
            "--policy",
            str(policy_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["tool"] == "exec.poll"
    assert captured["tool"] == "exec.poll"
    assert captured["args"]["session_id"] == "execproc_abcd"
    assert captured["args"]["tail_lines"] == 150
