from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime import RuntimeContext

from openminion.tools.gws.plugin import (
    GwsToolPlugin,
    _h_auth_export,
    _h_call,
    _h_schema,
)


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


def test_plugin_registers_tools() -> None:
    registry = ToolRegistry()
    GwsToolPlugin().register(registry)
    names = set(registry.list().keys())
    assert "gws.call" in names
    assert "gws.schema" in names
    assert "gws.auth.setup" in names
    assert "gws.auth.login" in names
    assert "gws.auth.export" in names


def test_gws_plugin_has_interface_contract() -> None:
    plugin_instance = GwsToolPlugin()
    assert hasattr(plugin_instance, "contract_version")
    assert (
        plugin_instance.contract_version == "v1"
    )  # GWS_INTERFACE_VERSION from interfaces.py


def test_gws_call_parses_json_and_injects_auth_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GWS_TOKEN_ENV", "secret-token-value")
    monkeypatch.setenv("GWS_CREDS_ENV", "/tmp/fake-credentials.json")
    calls = _install_fake_process(
        monkeypatch, stdout_text='{"files":[{"id":"file-1"}]}'
    )

    ctx = _ctx(
        tmp_path,
        gws_cfg={
            "env": {
                "token_secret": "GWS_TOKEN_ENV",
                "credentials_file_secret": "GWS_CREDS_ENV",
                "impersonated_user": "admin@example.com",
            }
        },
    )
    result = _h_call(
        {
            "service": "drive",
            "resource_path": ["files"],
            "method": "list",
            "params": {"pageSize": 10},
        },
        ctx,
    )

    assert result["ok"] is True
    assert result["data"]["files"][0]["id"] == "file-1"
    assert "--params" in calls["argv"]
    assert '{"pageSize":10}' in calls["argv"]
    assert calls["env"]["GOOGLE_WORKSPACE_CLI_TOKEN"] == "secret-token-value"
    assert (
        calls["env"]["GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE"]
        == "/tmp/fake-credentials.json"
    )
    assert calls["env"]["GOOGLE_WORKSPACE_CLI_IMPERSONATED_USER"] == "admin@example.com"

    audit_text = (Path(ctx.run_root) / "audit.jsonl").read_text(encoding="utf-8")
    assert "secret-token-value" not in audit_text


def test_gws_call_write_requires_confirmation(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, confirm=False)
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_call(
            {
                "service": "sheets",
                "resource_path": ["spreadsheets"],
                "method": "create",
                "json": {"properties": {"title": "Q1 Budget"}},
            },
            ctx,
        )
    assert exc_info.value.code == "POLICY_DENIED"
    assert (
        "gws write operations require explicit confirmation" in exc_info.value.message
    )


def test_gws_call_pagination_ndjson_and_large_output_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_fake_process(monkeypatch, stdout_text='{"page":1}\n{"page":2}\n')
    ctx = _ctx(tmp_path, gws_cfg={"max_raw_stdout_bytes": 8})

    result = _h_call(
        {
            "service": "drive",
            "resource_path": ["files"],
            "method": "list",
            "pagination": {"page_all": True},
            "expect_large_output": True,
        },
        ctx,
    )

    assert result["ok"] is True
    assert result["data_format"] == "ndjson"
    assert len(result["data"]) == 2
    assert result["raw_stdout"] is None
    assert "--page-all" in calls["argv"]
    assert "--page-limit" in calls["argv"]
    assert "--page-delay" in calls["argv"]


def test_gws_schema_invokes_schema_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_fake_process(
        monkeypatch,
        stdout_text='{"request":{"type":"object"},"response":{"type":"object"}}',
    )
    ctx = _ctx(tmp_path)
    result = _h_schema({"method_full": "drive.files.list"}, ctx)

    assert result["ok"] is True
    assert result["data"]["request"]["type"] == "object"
    assert calls["argv"][:3] == ["/usr/local/bin/gws", "schema", "drive.files.list"]


def test_gws_auth_export_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_process(monkeypatch)
    ctx = _ctx(tmp_path, confirm=False)
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_auth_export({"output_path": ".openminion/gws/credentials.json"}, ctx)
    assert exc_info.value.code == "POLICY_DENIED"


def test_gws_auth_export_writes_file_without_logging_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_process(
        monkeypatch, stdout_text='{"refresh_token":"r1","client_id":"cid"}'
    )
    ctx = _ctx(tmp_path, confirm=True)

    result = _h_auth_export(
        {"output_path": ".openminion/gws/credentials.json", "overwrite": True}, ctx
    )

    assert result["ok"] is True
    written = Path(result["data"]["output_path"])
    assert written.exists()
    assert "refresh_token" in written.read_text(encoding="utf-8")
    assert result["raw_stdout"] is None

    audit_text = (Path(ctx.run_root) / "audit.jsonl").read_text(encoding="utf-8")
    assert '"refresh_token":"r1"' not in audit_text


def test_gws_call_denied_when_service_is_in_deny_list(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, gws_cfg={"safety": {"deny_services": ["drive"]}})

    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_call({"service": "drive", "resource_path": ["files"], "method": "list"}, ctx)

    assert exc_info.value.code == "POLICY_DENIED"
    assert (
        "Service is denied by tools.gws.safety.deny_services" in exc_info.value.message
    )
    assert exc_info.value.details["service"] == "drive"


def test_gws_call_pagination_handling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _install_fake_process(monkeypatch, stdout_text='{"response": "success"}')
    ctx = _ctx(tmp_path)

    result = _h_call(
        {
            "service": "drive",
            "resource_path": ["files"],
            "method": "list",
            "pagination": {"page_all": True, "page_limit": 50, "page_delay_ms": 500},
        },
        ctx,
    )

    assert result["ok"] is True
    assert "--page-all" in calls["argv"]
    assert "--page-limit" in calls["argv"]
    assert "50" in calls["argv"]
    assert "--page-delay" in calls["argv"]
    assert "500" in calls["argv"]


def test_gws_call_pagination_large_output_and_suppression(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stdout_response = '{"item": 1}\n{"item": 2}\n{"item": 3}\n'
    _install_fake_process(monkeypatch, stdout_text=stdout_response)
    ctx = _ctx(tmp_path, gws_cfg={"max_raw_stdout_bytes": 10})

    result = _h_call(
        {
            "service": "drive",
            "resource_path": ["files"],
            "method": "list",
            "pagination": {"page_all": True},
            "expect_large_output": True,
        },
        ctx,
    )

    assert result["ok"] is True
    assert result["data_format"] == "ndjson"
    assert len(result["data"]) == 3
    assert result["raw_stdout"] is None


def test_gws_auth_export_path_policy_violation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake_process(
        monkeypatch, stdout_text='{"refresh_token":"r1","client_id":"cid"}'
    )

    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    policy = Policy(
        raw={
            "workspace_root": str(tmp_path / "runs"),
            "plugins": {"allow": ["openminion.tools.gws"], "deny": []},
            "tools": {
                "allow_prefix": ["gws_"],
                "deny_exact": [],
                "deny_prefix": [],
                "gws": {},
            },
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace / "safe_folder")],
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
    ctx = RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=True,
    )

    with pytest.raises(Exception) as exc_info:
        _h_auth_export({"output_path": "credentials.json", "overwrite": True}, ctx)

    error_str = str(exc_info.value).lower()
    assert any(
        keyword in error_str for keyword in ["path", "denied", "policy", "allow"]
    )
