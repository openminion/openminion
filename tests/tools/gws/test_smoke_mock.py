from __future__ import annotations

import subprocess
from pathlib import Path
import json

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.runtime import RuntimeContext
from openminion.tools.gws.plugin import _h_call, _h_schema


def _ctx(
    tmp_path: Path, *, confirm: bool = False, gws_cfg: dict | None = None
) -> RuntimeContext:
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    tools_cfg: dict = {
        "allow_prefix": ["gws_"],
        "deny_exact": [],
        "deny_prefix": [],
    }
    if gws_cfg is not None:
        tools_cfg["gws"] = gws_cfg

    policy = Policy(
        raw={
            "workspace_root": str(tmp_path / "runs"),
            "plugins": {"allow": ["openminion.tools.gws"], "deny": []},
            "tools": tools_cfg,
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "commands": {
                "mode": "allowlist",
                "allow": ["echo"],
                "deny_exact": [],
                "deny_regex": [],
            },
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=confirm,
    )


def _install_fake_process(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stdout_text: str = "",
    stderr_text: str = "",
    exit_code: int = 0,
    timeout: bool = False,
) -> dict:
    calls: dict = {}
    stdout_bytes = stdout_text.encode("utf-8")
    stderr_bytes = stderr_text.encode("utf-8")

    class _FakePopen:
        def __init__(self, argv, stdout, stderr, env):
            calls["argv"] = list(argv)
            calls["env"] = dict(env)
            stdout.write(stdout_bytes)
            stderr.write(stderr_bytes)
            if timeout:
                self.returncode = -9  # Simulate killed process return code
            else:
                self.returncode = int(exit_code)

        def wait(self, timeout=None):
            calls["timeout"] = timeout
            if timeout is not None and self.returncode == -9:
                raise subprocess.TimeoutExpired(
                    cmd=calls.get("argv", []), timeout=float(timeout)
                )
            return self.returncode

        def kill(self):
            calls["killed"] = True
            self.returncode = -9

    monkeypatch.setattr("openminion.tools.gws.plugin.subprocess.Popen", _FakePopen)
    monkeypatch.setattr(
        "openminion.tools.gws.plugin.shutil.which",
        lambda name: "/usr/local/bin/gws" if name == "gws" else None,
    )
    return calls


def test_gws_smoke_read_call_mocks_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    sample_files_response = json.dumps(
        {
            "files": [
                {"id": "file123", "name": "document.pdf", "mimeType": "application/pdf"}
            ]
        }
    )

    calls = _install_fake_process(monkeypatch, stdout_text=sample_files_response)

    ctx = _ctx(tmp_path)
    result = _h_call(
        {
            "service": "drive",
            "resource_path": ["files"],
            "method": "list",
            "params": {"pageSize": 3},
        },
        ctx,
    )

    assert result["ok"] is True
    assert result["source"] == "gws"
    assert "completed" in result["content"]
    assert isinstance(result["data"], dict)
    assert "files" in result["data"]
    assert len(result["data"]["files"]) == 1

    assert "--params" in calls["argv"]
    assert "pageSize" in calls["argv"][-1]

    audit_path = Path(ctx.run_root) / "audit.jsonl"
    if audit_path.exists():
        audit_text = audit_path.read_text(encoding="utf-8")
        assert len(audit_text) > 0


def test_gws_smoke_schema_call_mocks_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    example_schema = json.dumps(
        {
            "request": {
                "type": "object",
                "properties": {
                    "pageSize": {"type": "integer", "minimum": 1, "maximum": 1000}
                },
            },
            "response": {
                "type": "object",
                "properties": {
                    "files": {"type": "array", "items": {"$ref": "#/definitions/File"}}
                },
            },
        }
    )

    calls = _install_fake_process(monkeypatch, stdout_text=example_schema)

    ctx = _ctx(tmp_path)
    result = _h_schema({"method_full": "drive.files.list"}, ctx)

    assert result["ok"] is True
    assert result["source"] == "gws"
    assert "Schema fetched for drive.files.list" in result["content"]
    assert "request" in result["data"]
    assert "response" in result["data"]

    assert calls["argv"][:3] == ["/usr/local/bin/gws", "schema", "drive.files.list"]


def test_gws_smoke_write_call_dry_run_mocks_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    expected_success_msg = '{"result": "dry-run success", "affected_operations": 3}'

    calls = _install_fake_process(monkeypatch, stdout_text=expected_success_msg)

    ctx = _ctx(tmp_path, confirm=True)
    result = _h_call(
        {
            "service": "sheets",
            "resource_path": ["spreadsheets"],
            "method": "batchUpdate",
            "json": {
                "requests": [
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "dimension": "ROWS",
                                "startIndex": 0,
                                "endIndex": 2,
                            }
                        }
                    }
                ]
            },
            "dry_run": True,
            "force_risk": "write",
        },
        ctx,
    )

    assert result["ok"] is True
    assert result["source"] == "gws"
    assert "completed" in result["content"]
    assert result.get("risk") in ["write", "read", "admin"]

    assert "--dry-run" in calls["argv"]


def test_gws_smoke_write_call_rejected_without_confirm(tmp_path: Path):
    ctx = _ctx(tmp_path, confirm=False)

    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_call(
            {
                "service": "sheets",
                "resource_path": ["spreadsheets"],
                "method": "batchUpdate",
                "json": {
                    "requests": [
                        {
                            "updateDimensionProperties": {
                                "range": {
                                    "dimension": "ROWS",
                                    "startIndex": 0,
                                    "endIndex": 2,
                                }
                            }
                        }
                    ]
                },
            },
            ctx,
        )

    assert exc_info.value.code == "POLICY_DENIED"
    assert "require explicit confirmation" in exc_info.value.message


def test_gws_smoke_error_scenarios_handled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    error_response = json.dumps(
        {
            "error": {
                "code": 403,
                "message": "The caller does not have permission",
                "status": "PERMISSION_DENIED",
            }
        }
    )

    stderr_error = "Google Workspace API error: insufficient permissions"

    _install_fake_process(
        monkeypatch, stdout_text=error_response, stderr_text=stderr_error, exit_code=1
    )

    ctx = _ctx(tmp_path)
    result = _h_call(
        {
            "service": "drive",
            "resource_path": ["files"],
            "method": "list",
        },
        ctx,
    )

    assert result["ok"] is False
    assert result["error"] is not None
    error_code = result["error"]["code"]
    code_matches = any(
        code_to_look_for in str(error_code)
        for code_to_look_for in ["GWS_ERROR", "PERMISSION_DENIED", "403"]
    )
    assert code_matches
    assert "permission" in result["error"]["message"].lower()

    audit_path = Path(ctx.run_root) / "audit.jsonl"
    if audit_path.exists():
        audit_text = audit_path.read_text(encoding="utf-8")
        assert (
            "PERMISSION_DENIED" in audit_text
            or result["error"]["message"] in audit_text
        )
