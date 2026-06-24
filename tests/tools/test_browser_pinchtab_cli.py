from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from typer.testing import CliRunner

from openminion.modules.tool.cli import _invoke_pinchtab_tool, app
from openminion.modules.tool.contracts.schemas import (
    ErrorInfo,
    ResultEnvelope,
    WorkspaceInfo,
)


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


def _error_envelope(tool: str, *, code: str, message: str) -> ResultEnvelope:
    now = datetime.now(timezone.utc).isoformat()
    return ResultEnvelope(
        ok=False,
        tool=tool,
        run_id="run-1",
        request_id="req-1",
        policy_scope="WRITE_SAFE",
        started_at=now,
        ended_at=now,
        duration_ms=1,
        workspace=WorkspaceInfo(root=".", relative_root="."),
        error=ErrorInfo(code=code, message=message, details={}),
    )


def test_browser_pinchtab_health_routes_to_tool(monkeypatch, workspace_fixture):
    _workspace, policy_path = workspace_fixture
    runner = CliRunner()

    monkeypatch.setattr(
        "openminion.modules.tool.cli._invoke_pinchtab_tool",
        lambda **_kwargs: (_ok_envelope("browser.pinchtab.health", {"ok": True}), 0),
    )

    result = runner.invoke(
        app, ["browser", "pinchtab", "health", "--policy", str(policy_path), "--json"]
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["tool"] == "browser.pinchtab.health"
    assert payload["data"]["ok"] is True


def test_browser_pinchtab_snapshot_out_writes_file(
    monkeypatch, tmp_path: Path, workspace_fixture
):
    _workspace, policy_path = workspace_fixture
    runner = CliRunner()
    out_path = tmp_path / "snapshot.json"

    monkeypatch.setattr(
        "openminion.modules.tool.cli._invoke_pinchtab_tool",
        lambda **_kwargs: (
            _ok_envelope(
                "browser.pinchtab.snapshot",
                {"snapshot": {"nodes": [{"ref": "e1"}]}, "summary": [{"ref": "e1"}]},
            ),
            0,
        ),
    )

    result = runner.invoke(
        app,
        [
            "browser",
            "pinchtab",
            "tab",
            "snapshot",
            "--tab-id",
            "tab-1",
            "--out",
            str(out_path),
            "--policy",
            str(policy_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert out_path.exists()
    persisted = json.loads(out_path.read_text(encoding="utf-8"))
    assert persisted["nodes"][0]["ref"] == "e1"


def test_invoke_pinchtab_tool_maps_to_browser_call(monkeypatch, workspace_fixture):
    workspace, policy_path = workspace_fixture
    captured: dict[str, object] = {}

    def _fake_execute_call_payload(**kwargs):
        payload = json.loads(kwargs["payload"])
        captured.update(payload)
        return _ok_envelope(payload["tool"], {"mapped_args": payload["args"]}), 0

    monkeypatch.setattr(
        "openminion.modules.tool.cli._execute_call_payload", _fake_execute_call_payload
    )

    env, code = _invoke_pinchtab_tool(
        tool="browser.pinchtab.instance_start",
        args={"profile_id": "auth", "mode": "headed"},
        policy=policy_path,
        workspace=workspace,
        scope=None,
        confirm=False,
        timeout_sec=None,
    )

    assert code == 0
    assert env.ok is True
    assert captured["tool"] == "browser"
    assert captured["args"] == {
        "op": "instance.start",
        "provider": "pinchtab",
        "profile": "auth",
        "mode": "headed",
    }


def test_invoke_pinchtab_tool_falls_back_when_browser_tool_missing(
    monkeypatch, workspace_fixture
):
    workspace, policy_path = workspace_fixture
    captured_tools: list[str] = []

    def _fake_execute_call_payload(**kwargs):
        payload = json.loads(kwargs["payload"])
        captured_tools.append(payload["tool"])
        if payload["tool"] == "browser":
            return _error_envelope(
                "browser", code="NOT_FOUND", message="Unknown tool: browser"
            ), 1
        return _ok_envelope(payload["tool"], {"ok": True}), 0

    monkeypatch.setattr(
        "openminion.modules.tool.cli._execute_call_payload", _fake_execute_call_payload
    )

    env, code = _invoke_pinchtab_tool(
        tool="browser.pinchtab.health",
        args={},
        policy=policy_path,
        workspace=workspace,
        scope=None,
        confirm=False,
        timeout_sec=None,
    )

    assert code == 0
    assert env.ok is True
    assert captured_tools == ["browser", "browser.pinchtab.health"]
