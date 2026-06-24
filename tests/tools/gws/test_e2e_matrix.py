from __future__ import annotations

import subprocess
from pathlib import Path
import json

import pytest

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


def test_e2e_scenario_GWSII_E2E_01_cli_drive_file_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    mock_files_response = json.dumps(
        {
            "files": [
                {
                    "id": "file123",
                    "name": "document1.pdf",
                    "mimeType": "application/pdf",
                },
                {
                    "id": "file456",
                    "name": "spreadsheet1.xlsx",
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                },
                {
                    "id": "file789",
                    "name": "presentation1.pptx",
                    "mimeType": "application/vnd.google-apps.presentation",
                },
            ]
        }
    )

    calls = _install_fake_process(monkeypatch, stdout_text=mock_files_response)

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
    assert "source" in result
    assert "data" in result
    assert "files" in result["data"]
    assert isinstance(result["data"]["files"], list)
    assert len(result["data"]["files"]) == 3

    assert calls["argv"][1:4] == ["drive", "files", "list"]
    assert "--params" in calls["argv"]
    param_idx = calls["argv"].index("--params")
    assert (
        "pageSize" in calls["argv"][param_idx + 1]
        and "3" in calls["argv"][param_idx + 1]
    )


def test_e2e_scenario_GWSII_E2E_02_cli_schema_inspection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    mock_schema_response = json.dumps(
        {
            "request": {
                "type": "object",
                "properties": {
                    "pageSize": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "pageToken": {"type": "string"},
                },
            },
            "response": {
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "items": {"$ref": "#/definitions/DriveFile"},
                    }
                },
            },
            "definitions": {
                "DriveFile": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "mimeType": {"type": "string"},
                    },
                }
            },
        }
    )

    calls = _install_fake_process(monkeypatch, stdout_text=mock_schema_response)

    ctx = _ctx(tmp_path)
    result = _h_schema({"method_full": "drive.files.list"}, ctx)

    assert result["ok"] is True
    assert result["source"] == "gws"
    assert result["data"]["request"]["type"] == "object"
    assert result["data"]["response"]["type"] == "object"

    assert calls["argv"][:3] == ["/usr/local/bin/gws", "schema", "drive.files.list"]


def test_e2e_scenario_GWSII_E2E_03_cli_write_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    mock_creation_response = json.dumps(
        {
            "result": "dry-run successful",
            "spreadsheetId": "new-ss-id",
            "createdSpreadsheet": {"properties": {"title": "Test Spreadsheet"}},
        }
    )

    calls = _install_fake_process(monkeypatch, stdout_text=mock_creation_response)

    ctx = _ctx(tmp_path, confirm=True)
    result = _h_call(
        {
            "service": "sheets",
            "resource_path": ["spreadsheets"],
            "method": "create",
            "json": {"properties": {"title": "Test"}},
            "dry_run": True,
        },
        ctx,
    )

    assert result["ok"] is True
    assert result["source"] == "gws"

    assert result["risk"] in ["write", "admin"]

    assert "sheets" in calls["argv"]
    assert "spreadsheets" in calls["argv"]
    assert "create" in calls["argv"]
    assert "--json" in calls["argv"]
    assert "--dry-run" in calls["argv"]


def test_e2e_scenario_end_to_end_workflow_sequence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    drive_list_response = json.dumps(
        {"files": [{"id": "test-file-id", "name": "test-document.doc"}]}
    )

    calls_log = []

    def _make_install_process_fn(response_text, call_desc):
        def install_process(*args, **kwargs):
            kwargs["stdout_text"] = response_text
            new_calls = _install_fake_process(*args, **kwargs)
            new_calls["_desc"] = call_desc
            calls_log.append(new_calls)
            return new_calls

        return install_process

    _install_fake_process(monkeypatch, stdout_text=drive_list_response)
    ctx = _ctx(tmp_path)
    list_result = _h_call(
        {"service": "drive", "resource_path": ["files"], "method": "list"},
        ctx,
    )

    assert list_result["ok"] is True
    assert "files" in list_result["data"]

    schema_response = json.dumps(
        {"request": {"type": "object"}, "response": {"type": "object"}}
    )
    _install_fake_process(monkeypatch, stdout_text=schema_response)
    schema_result = _h_schema({"method_full": "drive.files.get"}, ctx)

    assert schema_result["ok"] is True
    assert "request" in schema_result["data"]

    write_response = json.dumps({"status": "dry-run-success"})
    _install_fake_process(monkeypatch, stdout_text=write_response)
    ctx_with_confirm = _ctx(tmp_path, confirm=True)
    write_result = _h_call(
        {
            "service": "drive",
            "resource_path": ["files"],
            "method": "update",
            "json": {"name": "renamed-file.doc"},
            "dry_run": True,
        },
        ctx_with_confirm,
    )

    assert write_result["ok"] is True
    assert write_result["risk"] in ["read", "write", "admin"]

    assert list_result["ok"] and schema_result["ok"] and write_result["ok"]
