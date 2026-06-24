from __future__ import annotations

import json
import time
from pathlib import Path

from typer.testing import CliRunner

from openminion.modules.tool.cli import app
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.contracts.schemas import SysInfoArgs


def _policy_file(tmp_path: Path) -> Path:
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        """
version: 1
scope: WRITE_SAFE
workspace_root: "{workspace_root}"
tools:
  allow_prefix: ["cmd.","file.","proc.","sys."]
paths:
  read_allow: ["${{WORKSPACE}}"]
  write_allow: ["${{WORKSPACE}}"]
commands:
  mode: allowlist
  allow: ["python","python3.11","echo"]
""".format(workspace_root=str(tmp_path / "runs"))
    )
    return policy


def _policy_file_with_slow_tool(tmp_path: Path) -> Path:
    policy = tmp_path / "policy_slow.yaml"
    policy.write_text(
        """
version: 1
scope: WRITE_SAFE
workspace_root: "{workspace_root}"
tools:
  allow_prefix: ["slow."]
paths:
  read_allow: ["${{WORKSPACE}}"]
  write_allow: ["${{WORKSPACE}}"]
commands:
  mode: allowlist
  allow: ["python","python3.11","echo"]
""".format(workspace_root=str(tmp_path / "runs"))
    )
    return policy


def test_call_returns_json_envelope_when_run_root_creation_fails(monkeypatch, tmp_path):
    runner = CliRunner()
    policy_path = _policy_file(tmp_path)

    def _boom(*_args, **_kwargs):
        raise ToolRuntimeError(
            "EXEC_ERROR", "run root failed", {"stage": "create_run_root"}
        )

    monkeypatch.setattr("openminion.modules.tool.cli.create_run_root", _boom)

    result = runner.invoke(
        app,
        [
            "call",
            '{"tool":"cmd.which","args":{"name":"python3.11"}}',
            "--policy",
            str(policy_path),
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "EXEC_ERROR"
    assert payload["error"]["details"]["stage"] == "create_run_root"


def test_call_dry_run_skips_run_root_creation(monkeypatch, tmp_path):
    runner = CliRunner()
    policy_path = _policy_file(tmp_path)

    def _should_not_run(*_args, **_kwargs):
        raise AssertionError("create_run_root should not be called for dry_run")

    monkeypatch.setattr("openminion.modules.tool.cli.create_run_root", _should_not_run)

    result = runner.invoke(
        app,
        [
            "call",
            '{"tool":"cmd.which","args":{"name":"python3.11"},"meta":{"dry_run":true}}',
            "--policy",
            str(policy_path),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["data"]["dry_run"] is True


def test_call_timeout_sec_rejects_non_positive_value(tmp_path):
    runner = CliRunner()
    policy_path = _policy_file(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    result = runner.invoke(
        app,
        [
            "call",
            '{"tool":"cmd.which","args":{"name":"python3.11"}}',
            "--policy",
            str(policy_path),
            "--workspace",
            str(workspace),
            "--timeout-sec",
            "0",
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_ARGUMENT"


def test_call_timeout_sec_enforces_outer_timeout(monkeypatch, tmp_path):
    runner = CliRunner()
    policy_path = _policy_file_with_slow_tool(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    def _slow_handler(args, ctx):
        del args, ctx
        time.sleep(2)
        return {"slept": True}

    def _build_registry(_policy):
        reg = ToolRegistry()
        reg.add(ToolSpec("slow.sleep", SysInfoArgs, "READ_ONLY", _slow_handler))
        return reg, []

    monkeypatch.setattr("openminion.modules.tool.cli._build_registry", _build_registry)

    result = runner.invoke(
        app,
        [
            "call",
            '{"tool":"slow.sleep","args":{}}',
            "--policy",
            str(policy_path),
            "--workspace",
            str(workspace),
            "--timeout-sec",
            "1",
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "TIMEOUT"
