from __future__ import annotations

import json
import time
from typing import get_args

import pytest

from openminion.base.runtime.sandbox import ExecResult
from openminion.modules.brain.runtime.escalation import ApprovalResponse
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime import RuntimeContext
from openminion.services.runtime.daytona.client import DaytonaClientError

import openminion.tools.exec.plugin as exec_plugin
from openminion.tools.exec.plugin import (
    _artifactize_output,
    register,
    _h_exec_run,
    _h_process_poll,
    _h_process_send_keys,
    _validate_command_against_policy,
    _validate_host_allowlist,
)
from openminion.tools.exec.process import PROCESS_MANAGER
from openminion.tools.exec.process import ShellFamily


@pytest.fixture(autouse=True)
def _cleanup_sessions():
    PROCESS_MANAGER._reset_for_tests()
    try:
        yield
    finally:
        PROCESS_MANAGER._reset_for_tests()


def _ctx(tmp_path, *, sandbox_runner=None, env=None):
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    policy = Policy(
        raw={
            "workspace_root": str(tmp_path / "runs"),
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "commands": {
                "mode": "allowlist",
                "allow": [
                    "bash",
                    "zsh",
                    "sh",
                    "pwsh",
                    "powershell",
                    "cmd.exe",
                    "printf",
                    "sleep",
                    "echo",
                    "cat",
                ],
                "deny_exact": [],
                "deny_regex": [],
            },
            "env": {"allow_keys": ["PATH", "HOME"], "deny_keys_regex": []},
        }
    )
    kwargs = {}
    if env is not None:
        kwargs["env"] = env
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
        sandbox_runner=sandbox_runner,
        **kwargs,
    )


class _RecordingSandboxRunner:
    def __init__(self, result: ExecResult) -> None:
        self.result = result
        self.calls: list[tuple[object, object]] = []

    def run_exec(self, spec, sandbox):
        self.calls.append((spec, sandbox))
        return self.result


class _FailingSandboxRunner:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def run_exec(self, spec, sandbox):
        del spec, sandbox
        raise self.exc


class _CASArtifactRef:
    def __init__(self, ref: str, sha256: str) -> None:
        self.ref = ref
        self.sha256 = sha256


class _RecordingArtifactCtl:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def ingest_bytes(self, **kwargs):
        self.calls.append(dict(kwargs))
        return _CASArtifactRef("artifact://sha256/" + ("d" * 64), "d" * 64)


class _FailingArtifactCtl:
    def ingest_bytes(self, **kwargs):
        del kwargs
        raise RuntimeError("cas unavailable")


def test_plugin_registers_tools():
    registry = ToolRegistry()
    register(registry)

    names = set(registry.list().keys())
    assert "exec.run" in names
    assert "exec.poll" in names
    assert "exec.send_keys" in names
    assert "exec.submit" in names
    assert "exec.paste" in names
    assert "exec.kill" in names
    assert "exec.clear" in names
    assert "exec.list" in names


def test_exec_run_foreground_returns_ok_and_preview(tmp_path):
    ctx = _ctx(tmp_path)
    result = _h_exec_run({"command": "printf 'hello\\n'"}, ctx)

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert "hello" in str(result.get("stdout_preview") or "")


def test_exec_run_missing_toolchain_discovery_result_stops_retry_loop(tmp_path):
    ctx = _ctx(tmp_path)
    missing_tool = "openminion_missing_toolchain_probe_zzzz"
    ctx.policy.raw["commands"]["known_tools"] = [missing_tool]

    result = _h_exec_run({"command": f"command -v {missing_tool}"}, ctx)

    assert result["status"] == "ok"
    assert result["exit_code"] == 1
    assert result["summary"] == f"Toolchain discovery did not find {missing_tool}."
    assert result["error"] is None


def test_exec_run_accepts_common_model_argument_aliases(tmp_path):
    ctx = _ctx(tmp_path)

    result = _h_exec_run(
        {
            "command_line": "printf 'hello\\n'",
            "cwd": str(tmp_path / "workspace"),
            "timeout_secs": "60",
            "env": "{}",
            "description": "Run a quick verification command",
            "stderr_to_stdout": "true",
        },
        ctx,
    )

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["stdout_preview"] == "hello\n"


def test_exec_run_accepts_workdir_and_timeout_ms_aliases(tmp_path):
    ctx = _ctx(tmp_path)

    result = _h_exec_run(
        {
            "command": "printf 'hello\\n'",
            "working_directory": str(tmp_path / "workspace"),
            "timeout_ms": "30000",
            "daemon": "false",
        },
        ctx,
    )

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["stdout_preview"] == "hello\n"


def test_exec_run_accepts_path_as_workdir_alias(tmp_path):
    ctx = _ctx(tmp_path)

    result = _h_exec_run(
        {
            "command": "printf 'hello\\n'",
            "path": str(tmp_path / "workspace"),
        },
        ctx,
    )

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert result["stdout_preview"] == "hello\n"


def test_exec_run_foreground_contract_snapshot(tmp_path):
    ctx = _ctx(tmp_path)

    result = _h_exec_run({"command": "printf 'hello\\n'"}, ctx)

    metrics = dict(result["metrics"])
    assert isinstance(metrics.pop("duration_ms"), int)
    assert metrics == {
        "bytes_err": 0,
        "bytes_out": 6,
        "retries": 0,
    }
    normalized = dict(result)
    normalized["metrics"] = metrics
    assert normalized == {
        "approval_id": None,
        "approval_response": None,
        "error": None,
        "exit_code": 0,
        "metrics": {
            "bytes_err": 0,
            "bytes_out": 6,
            "retries": 0,
        },
        "risk_tier": "silent",
        "session_id": None,
        "status": "ok",
        "stderr": None,
        "stderr_artifact": None,
        "stderr_preview": None,
        "stdout": "hello\n",
        "stdout_artifact": None,
        "stdout_preview": "hello\n",
        "summary": "Command exited with code 0.\n\nstdout:\nhello",
    }


def test_exec_run_accepts_cmd_alias(tmp_path):
    ctx = _ctx(tmp_path)
    result = _h_exec_run({"cmd": "printf 'hello\\n'"}, ctx)

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert "hello" in str(result.get("stdout_preview") or "")


def test_exec_run_uses_shared_sandbox_runner_for_foreground_sandbox(tmp_path):
    runner = _RecordingSandboxRunner(
        ExecResult(returncode=0, stdout="runner hello\n", stderr="")
    )
    ctx = _ctx(tmp_path, sandbox_runner=runner)

    result = _h_exec_run({"command": "printf 'runner hello\\n'"}, ctx)

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert "runner hello" in str(result.get("stdout_preview") or "")
    assert len(runner.calls) == 1
    spec, sandbox = runner.calls[0]
    assert (
        spec.cmd[0].endswith("sh")
        or spec.cmd[0].endswith("bash")
        or spec.cmd[0].endswith("zsh")
    )
    assert sandbox.workspace_root == str(ctx.workspace)
    assert sandbox.session_mode == "foreground"


def test_exec_run_background_with_sandbox_runner_returns_session_unsupported(tmp_path):
    runner = _RecordingSandboxRunner(
        ExecResult(returncode=0, stdout="runner hello\n", stderr="")
    )
    ctx = _ctx(tmp_path, sandbox_runner=runner)

    started = _h_exec_run({"command": "sleep 0.1; echo done", "background": True}, ctx)

    assert started["status"] == "error"
    assert started["error"]["code"] == "SANDBOX_SESSION_UNSUPPORTED"
    assert runner.calls == []


def test_exec_run_maps_daytona_client_errors_to_typed_exec_errors(tmp_path):
    runner = _FailingSandboxRunner(
        DaytonaClientError(
            code="SANDBOX_NETWORK_DENIED",
            message="network denied",
        )
    )
    ctx = _ctx(tmp_path, sandbox_runner=runner)

    result = _h_exec_run({"command": "printf 'hello\\n'"}, ctx)

    assert result["status"] == "error"
    assert result["error"]["code"] == "SANDBOX_NETWORK_DENIED"
    assert result["error"]["message"] == "network denied"


def test_exec_run_maps_sandbox_timeout_to_resource_limit_code(tmp_path):
    runner = _RecordingSandboxRunner(
        ExecResult(returncode=-1, stdout="", stderr="timeout", timed_out=True)
    )
    ctx = _ctx(tmp_path, sandbox_runner=runner)

    result = _h_exec_run({"command": "printf 'hello\\n'", "timeout_s": 7}, ctx)

    assert result["status"] == "timeout"
    assert result["error"]["code"] == "SANDBOX_RESOURCE_LIMIT"


def test_exec_run_denies_non_read_only_commands_for_watch_turns(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.policy.raw["context_metadata"] = {"watch_job": "true"}

    result = _h_exec_run({"command": "echo hello"}, ctx)

    assert result["status"] == "denied"
    assert result["error"]["code"] == "POLICY_DENIED"
    assert "read-only" in result["summary"].lower()


def test_exec_run_allows_quoted_separator_literal(tmp_path):
    ctx = _ctx(tmp_path)
    result = _h_exec_run({"command": 'echo "alpha;beta"'}, ctx)

    assert result["status"] == "ok"
    assert "alpha;beta" in str(result.get("stdout_preview") or "")


def test_exec_run_denies_unquoted_redirection(tmp_path):
    ctx = _ctx(tmp_path)
    result = _h_exec_run({"command": "echo hi > out.txt"}, ctx)

    assert result["status"] == "denied"
    assert result["error"]["details"]["parse_error_code"] == "unsupported_redirection"
    assert result["error"]["details"]["parse_error_position"] == 8
    assert result["error"]["details"]["suggested_tool"] == "file.list_dir"


def test_exec_run_mkdir_denial_includes_structured_file_hint(tmp_path):
    ctx = _ctx(tmp_path)

    result = _h_exec_run({"command": "mkdir -p nested/project"}, ctx)

    assert result["status"] == "denied"
    assert result["error"]["code"] == "POLICY_DENIED"
    assert result["error"]["details"]["suggested_tool"] == "file.write"
    assert "parent directories" in result["error"]["details"]["suggested_fix"]


def test_exec_run_find_denial_includes_structured_file_hint(tmp_path):
    ctx = _ctx(tmp_path)

    result = _h_exec_run(
        {"command": "find /tmp/workspace -type f"},
        ctx,
    )

    assert result["status"] == "denied"
    assert result["error"]["code"] == "POLICY_DENIED"
    assert result["error"]["details"]["suggested_tool"] == "file.find"
    fix = result["error"]["details"]["suggested_fix"]
    assert "file.find" in fix
    assert "file.list_dir" in fix


def test_exec_run_ls_denial_includes_structured_file_hint(tmp_path):
    ctx = _ctx(tmp_path)

    result = _h_exec_run({"command": "ls /tmp/workspace"}, ctx)

    assert result["status"] == "denied"
    assert result["error"]["code"] == "POLICY_DENIED"
    assert result["error"]["details"]["suggested_tool"] == "file.list_dir"
    assert "file.list_dir" in result["error"]["details"]["suggested_fix"]


def test_exec_run_grep_denial_includes_structured_file_hint(tmp_path):
    ctx = _ctx(tmp_path)

    result = _h_exec_run({"command": "grep -r needle /tmp/workspace"}, ctx)

    assert result["status"] == "denied"
    assert result["error"]["code"] == "POLICY_DENIED"
    assert result["error"]["details"]["suggested_tool"] == "file.search"
    assert "file.search" in result["error"]["details"]["suggested_fix"]


def test_exec_run_curl_denial_includes_structured_web_hint(tmp_path):
    ctx = _ctx(tmp_path)

    result = _h_exec_run(
        {"command": 'curl -s "https://packaging.python.org/"'},
        ctx,
    )

    assert result["status"] == "denied"
    assert result["error"]["code"] == "POLICY_DENIED"
    assert result["error"]["details"]["suggested_tool"] == "web.fetch"
    fix = result["error"]["details"]["suggested_fix"]
    assert "web.fetch" in fix
    assert "web.search" in fix


def test_exec_run_head_tail_denial_include_structured_file_hint(tmp_path):
    ctx = _ctx(tmp_path)

    for command in ("head -n 5 /tmp/workspace/a.txt", "tail -n 5 /tmp/workspace/a.txt"):
        result = _h_exec_run({"command": command}, ctx)
        assert result["status"] == "denied"
        assert result["error"]["code"] == "POLICY_DENIED"
        assert result["error"]["details"]["suggested_tool"] == "file.read"
        assert "file.read" in result["error"]["details"]["suggested_fix"]


def test_exec_run_cd_prefix_normalizes_to_workdir(tmp_path):
    ctx = _ctx(tmp_path)
    nested = ctx.workspace / "nested" / "project"
    nested.mkdir(parents=True, exist_ok=True)

    result = _h_exec_run({"command": "cd nested/project && printf 'hi\\n'"}, ctx)

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert "hi" in str(result.get("stdout_preview") or "")


def test_exec_run_python3_alias_normalizes_to_python311(tmp_path, monkeypatch):
    runner = _RecordingSandboxRunner(
        ExecResult(returncode=0, stdout="Python 3.11.9\n", stderr="")
    )
    ctx = _ctx(tmp_path, sandbox_runner=runner)
    ctx.policy.raw["commands"]["allow"].extend(["python3", "python3.11"])

    real_which = exec_plugin.shutil.which

    def _fake_which(name: str) -> str | None:
        if name == "python3.11":
            return "/usr/local/bin/python3.11"
        return real_which(name)

    monkeypatch.setattr(exec_plugin.shutil, "which", _fake_which)

    result = _h_exec_run({"command": "python3 --version"}, ctx)

    assert result["status"] == "ok"
    spec, _sandbox = runner.calls[0]
    assert any("python3.11 --version" in str(part) for part in spec.cmd)


def test_exec_run_python_missing_normalizes_to_python311(tmp_path, monkeypatch):
    runner = _RecordingSandboxRunner(ExecResult(returncode=0, stdout="ok\n", stderr=""))
    ctx = _ctx(tmp_path, sandbox_runner=runner)
    ctx.policy.raw["commands"]["allow"].extend(["python", "python3.11"])

    real_which = exec_plugin.shutil.which

    def _fake_which(name: str) -> str | None:
        if name == "python":
            return None
        if name == "python3.11":
            return "/usr/local/bin/python3.11"
        return real_which(name)

    monkeypatch.setattr(exec_plugin.shutil, "which", _fake_which)

    result = _h_exec_run({"command": "python -m pytest -q tests"}, ctx)

    assert result["status"] == "ok"
    spec, _sandbox = runner.calls[0]
    assert any("python3.11 -m pytest -q tests" in str(part) for part in spec.cmd)


def test_exec_run_trailing_capture_redirection_normalizes_away(tmp_path):
    runner = _RecordingSandboxRunner(ExecResult(returncode=0, stdout="ok\n", stderr=""))
    ctx = _ctx(tmp_path, sandbox_runner=runner)
    ctx.policy.raw["commands"]["allow"].extend(["python", "python3.11"])

    result = _h_exec_run({"command": "python -m pytest -q tests 2>&1"}, ctx)

    assert result["status"] == "ok"
    spec, _sandbox = runner.calls[0]
    assert all("2>&1" not in str(part) for part in spec.cmd)


def test_exec_run_emits_debug_parse_event_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMINION_TOOL_EXEC_DEBUG_PARSE_EVENT", "1")
    ctx = _ctx(tmp_path)

    result = _h_exec_run({"command": 'echo "alpha;beta"'}, ctx)

    assert result["status"] == "ok"
    audit_path = ctx.run_root / "audit.jsonl"
    events = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    parse_events = [
        event for event in events if event.get("event") == "exec.command_parsed"
    ]
    assert len(parse_events) == 1
    parse_event = parse_events[0]
    assert parse_event["validator"] == "policy"
    assert parse_event["segment_count"] == 1
    assert parse_event["operators"] == []
    assert len(str(parse_event.get("command_hash") or "")) == 64


def test_exec_run_background_then_poll_until_exit(tmp_path):
    ctx = _ctx(tmp_path)
    started = _h_exec_run({"command": "sleep 0.2; echo done", "background": True}, ctx)
    assert started["status"] == "running"
    session_id = str(started["session_id"])

    combined = []
    final = {}
    for _ in range(30):
        polled = _h_process_poll({"session_id": session_id, "tail_lines": 200}, ctx)
        if polled.get("stdout_preview"):
            combined.append(str(polled["stdout_preview"]))
        if polled["status"] != "running":
            final = polled
            break
        time.sleep(0.05)

    assert final
    assert final["status"] == "exited"
    assert final["exit_code"] == 0
    assert "done" in "\n".join(combined)


def test_host_exec_returns_approval_pending_when_ask_enabled(tmp_path):
    import os

    os.environ["OPENMINION_TOOL_EXEC_ENABLE_HOST_EXEC"] = "1"
    ctx = _ctx(tmp_path)
    try:
        result = _h_exec_run(
            {
                "command": "echo hi",
                "host": "gateway",
                "security": "full",
                "ask": "always",
            },
            ctx,
        )
    finally:
        os.environ.pop("OPENMINION_TOOL_EXEC_ENABLE_HOST_EXEC", None)

    assert result["status"] == "approval-pending"
    assert result["risk_tier"] == "approve"
    assert str(result.get("approval_id") or "").startswith("approval_")
    assert result["approval_response"]["status"] == "pending"


def test_exec_run_result_uses_typed_approval_response_contract():
    validated = exec_plugin.ExecRunResult.model_validate(
        {
            "status": "approval-pending",
            "approval_response": {"status": "pending", "reason": "needs_user"},
        }
    )

    assert isinstance(validated.approval_response, ApprovalResponse)
    annotation = exec_plugin.ExecRunResult.model_fields["approval_response"].annotation
    assert ApprovalResponse in get_args(annotation)


def test_declared_exec_risk_tiers_do_not_use_name_heuristics():
    assert exec_plugin._declared_exec_risk_tier("exec.run") == "approve"
    assert exec_plugin._declared_exec_risk_tier("task.schedule") == "silent"
    assert exec_plugin._declared_exec_risk_tier("file.write") == "silent"


def test_send_keys_requires_pty_session(tmp_path):
    ctx = _ctx(tmp_path)
    started = _h_exec_run({"command": "sleep 1", "background": True, "pty": False}, ctx)
    session_id = str(started["session_id"])

    result = _h_process_send_keys({"session_id": session_id, "keys": ["Enter"]}, ctx)
    assert result["status"] == "error"
    assert result["error"]["code"] == "INVALID_REQUEST"


def test_exec_yield_auto_backgrounds(tmp_path):
    ctx = _ctx(tmp_path)
    started = _h_exec_run({"command": "sleep 0.5; echo done", "yield_ms": 100}, ctx)
    assert started["status"] == "running"
    session_id = started["session_id"]

    final = {}
    for _ in range(30):
        polled = _h_process_poll({"session_id": session_id, "tail_lines": 50}, ctx)
        if polled["status"] != "running":
            final = polled
            break
        time.sleep(0.05)
    assert final["status"] == "exited"


def test_process_submit_and_paste(tmp_path):
    ctx = _ctx(tmp_path)
    started = _h_exec_run({"command": "cat", "background": True, "pty": True}, ctx)
    assert started["status"] == "running", f"Failed to start cat: {started}"
    session_id = started["session_id"]

    from openminion.tools.exec.plugin import (
        _h_process_paste,
        _h_process_submit,
        _h_process_send_keys,
    )

    _h_process_paste(
        {"session_id": session_id, "text": "hello_paste", "bracketed": False}, ctx
    )
    _h_process_submit({"session_id": session_id}, ctx)
    _h_process_send_keys({"session_id": session_id, "keys": ["C-D"]}, ctx)

    final = {}
    combined_out = []
    for _ in range(30):
        polled = _h_process_poll({"session_id": session_id, "tail_lines": 50}, ctx)
        if polled.get("stdout_preview"):
            combined_out.append(str(polled["stdout_preview"]))
        if polled["status"] != "running":
            final = polled
            break
        time.sleep(0.05)
    assert final["status"] == "exited"
    assert "hello_paste" in "".join(combined_out)


def test_process_kill_and_clear(tmp_path):
    from openminion.tools.exec.plugin import _h_process_kill, _h_process_clear

    ctx = _ctx(tmp_path)
    started = _h_exec_run({"command": "sleep 10", "background": True}, ctx)
    session_id = started["session_id"]

    kill_result = _h_process_kill({"session_id": session_id}, ctx)
    assert kill_result["status"] == "ok"

    polled = _h_process_poll({"session_id": session_id, "tail_lines": 10}, ctx)
    assert polled["status"] == "killed"

    clear_result = _h_process_clear({"session_id": session_id}, ctx)
    assert clear_result["status"] == "ok"

    polled_again = _h_process_poll({"session_id": session_id, "tail_lines": 10}, ctx)
    assert polled_again["status"] == "killed"
    assert polled_again["error"]["code"] == "NOT_FOUND"


def test_process_timeout(tmp_path):
    ctx = _ctx(tmp_path)
    _h_exec_run({"command": "sleep 1.0", "yield_ms": 2000, "timeout_s": 1}, ctx)


def test_session_isolation(tmp_path):
    ctx1 = _ctx(tmp_path)
    ctx2 = _ctx(tmp_path)
    ctx2.policy.raw["agent_id"] = "agent-2"

    started = _h_exec_run({"command": "sleep 0.5", "background": True}, ctx1)
    session_id = started["session_id"]

    polled = _h_process_poll({"session_id": session_id, "tail_lines": 5}, ctx2)
    assert polled["status"] == "killed"
    assert polled["error"]["code"] == "NOT_FOUND"


def test_host_env_overrides_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMINION_TOOL_EXEC_ENABLE_HOST_EXEC", "1")
    ctx = _ctx(tmp_path)
    result = _h_exec_run(
        {
            "command": "echo hi",
            "host": "node",
            "security": "allowlist",
            "env": {"LD_PRELOAD": "lib.so"},
        },
        ctx,
    )
    assert result["status"] == "denied"
    assert "rejects dynamic loader env overrides" in result["error"]["message"]

    result2 = _h_exec_run(
        {
            "command": "echo hi",
            "host": "node",
            "security": "allowlist",
            "env": {"PATH": "/foo"},
        },
        ctx,
    )
    assert result2["status"] == "denied"
    assert "rejects env override PATH" in result2["error"]["message"]


def test_host_security_deny(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMINION_TOOL_EXEC_ENABLE_HOST_EXEC", "1")
    ctx = _ctx(tmp_path)
    result = _h_exec_run(
        {"command": "echo hi", "host": "gateway", "security": "deny"}, ctx
    )
    assert result["status"] == "denied"
    assert "denied by security mode" in result["error"]["message"]


def test_unsandboxed_exec_denied_by_default_uses_typed_error(tmp_path):
    ctx = _ctx(tmp_path)

    result = _h_exec_run(
        {"command": "echo hi", "host": "gateway", "security": "full", "ask": "off"},
        ctx,
    )

    assert result["status"] == "denied"
    assert result["error"]["code"] == "UNSANDBOXED_EXEC_DISABLED"
    assert "Unsandboxed execution is disabled" in result["summary"]


def test_host_security_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMINION_TOOL_EXEC_ENABLE_HOST_EXEC", "1")
    monkeypatch.setenv("OPENMINION_TOOL_EXEC_SAFE_BINS", "echo,sleep")
    ctx = _ctx(tmp_path)
    result = _h_exec_run(
        {"command": "echo hi", "host": "node", "security": "allowlist", "ask": "off"},
        ctx,
    )
    assert result["status"] == "ok"
    result2 = _h_exec_run(
        {"command": "whoami", "host": "node", "security": "allowlist", "ask": "off"},
        ctx,
    )
    assert result2["status"] == "denied"


def test_validate_command_against_policy_accepts_windows_shell_family_path(
    tmp_path,
    monkeypatch,
):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        exec_plugin,
        "resolve_shell_family",
        lambda: ShellFamily.POWERSHELL,
    )

    allowed, message, details = _validate_command_against_policy("echo hi", ctx)

    assert allowed
    assert message == ""
    assert details["checked"][0]["exec"] == "echo"


def test_validate_command_against_policy_windows_subset_denies_control_operators(
    tmp_path,
    monkeypatch,
):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        exec_plugin,
        "resolve_shell_family",
        lambda: ShellFamily.POWERSHELL,
    )

    allowed, message, details = _validate_command_against_policy(
        "echo hi && whoami",
        ctx,
    )

    assert not allowed
    assert "unsupported command syntax" in message
    assert details["parse_error_code"] == "unsupported_syntax"


def test_validate_command_against_policy_denies_unknown_shell_family(
    tmp_path,
    monkeypatch,
):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        exec_plugin,
        "resolve_shell_family",
        lambda: ShellFamily.UNKNOWN,
    )

    allowed, message, details = _validate_command_against_policy("echo hi", ctx)

    assert not allowed
    assert "unsupported shell family" in message
    assert details["shell_family"] == "unknown"
    assert details["parse_error_code"] == "unsupported_shell"
    assert details["parse_error_position"] is None


def test_validate_host_allowlist_reports_parse_error_metadata(tmp_path):
    ctx = _ctx(tmp_path)

    allowed, message, details = _validate_host_allowlist("echo hi > out.txt", ctx)

    assert not allowed
    assert "redirections are not supported" in message
    assert details["parse_error_code"] == "unsupported_redirection"
    assert details["parse_error_position"] == 8
    assert details["suggested_tool"] == "file.list_dir"


def test_validate_host_allowlist_redirection_hint_preserves_exec_for_pytest(tmp_path):
    ctx = _ctx(tmp_path)

    allowed, message, details = _validate_host_allowlist(
        "python -m pytest -q tests 2>&1 | head -60",
        ctx,
    )

    assert not allowed
    assert "redirections are not supported" in message
    assert details["parse_error_code"] == "unsupported_redirection"
    assert details["suggested_tool"] == "exec.run"
    assert "Run the verification command directly" in details["suggested_fix"]


def test_validate_command_against_policy_hints_python_module_for_bare_pytest(
    tmp_path,
):
    ctx = _ctx(tmp_path)

    allowed, message, details = _validate_command_against_policy(
        "pytest tests/ -v",
        ctx,
    )

    assert not allowed
    assert "pytest" in message
    assert details["suggested_tool"] == "exec.run"
    assert "python -m pytest -q tests" in details["suggested_fix"]
    assert "Bare `pytest` is not allowlisted" in details["suggested_fix"]


def test_validate_command_against_policy_hints_direct_pytest_for_pip_install(
    tmp_path,
):
    ctx = _ctx(tmp_path)

    allowed, message, details = _validate_command_against_policy(
        "pip install -e .",
        ctx,
    )

    assert not allowed
    assert "pip" in message
    assert details["suggested_tool"] == "exec.run"
    assert (
        "Package-manager install commands are not allowlisted"
        in details["suggested_fix"]
    )
    assert "python -m pytest -q tests" in details["suggested_fix"]


def test_validate_command_against_policy_hints_direct_pytest_for_cd_pip_install(
    tmp_path,
):
    ctx = _ctx(tmp_path)

    allowed, message, details = _validate_command_against_policy(
        "cd . && pip install -e .",
        ctx,
    )

    assert not allowed
    assert "cd" in message
    assert details["suggested_tool"] == "exec.run"
    assert (
        "Package-manager install commands are not allowlisted"
        in details["suggested_fix"]
    )
    assert "python -m pytest -q tests" in details["suggested_fix"]


def test_validate_command_against_policy_hints_split_for_toolchain_discovery_chain(
    tmp_path,
):
    ctx = _ctx(tmp_path)

    allowed, message, details = _validate_command_against_policy(
        "command -v nasm && nasm --version",
        ctx,
    )

    assert not allowed
    assert "split toolchain discovery" in message
    assert details["suggested_tool"] == "exec.run"
    assert "command -v nasm" in details["suggested_fix"]
    assert "nasm --version" in details["suggested_fix"]


def test_validate_command_against_policy_allows_direct_toolchain_discovery(
    tmp_path,
):
    ctx = _ctx(tmp_path)

    allowed, message, details = _validate_command_against_policy(
        "command -v nasm",
        ctx,
    )

    assert allowed
    assert message == ""
    assert details["checked"][0]["exec"] == "command"


def test_validate_command_against_policy_denies_non_discovery_toolchain_shapes(
    tmp_path,
):
    ctx = _ctx(tmp_path)

    for command in (
        "clang -v",
        "nasm -f macho64 ping.asm",
        "clang ping.s -o ping",
        "npm install left-pad",
        "./ping",
    ):
        allowed, message, details = _validate_command_against_policy(command, ctx)

        assert not allowed, command
        assert message
        assert details["action_class"] in {"compile", "install", "run"}


def test_validate_command_against_policy_keeps_cd_prefix_normalization_path(
    tmp_path,
):
    ctx = _ctx(tmp_path)

    allowed, message, details = _validate_command_against_policy(
        "cd . && pip install -e .",
        ctx,
    )

    assert not allowed
    assert "cd" in message
    assert details["suggested_tool"] == "exec.run"
    assert "Package-manager install commands are not allowlisted" in str(
        details["suggested_fix"]
    )


def test_validate_host_allowlist_denies_unknown_shell_family(
    tmp_path,
    monkeypatch,
):
    ctx = _ctx(tmp_path)
    monkeypatch.setattr(
        exec_plugin,
        "resolve_shell_family",
        lambda: ShellFamily.UNKNOWN,
    )

    allowed, message, details = _validate_host_allowlist("echo hi", ctx)

    assert not allowed
    assert "unsupported shell family" in message
    assert details["shell_family"] == "unknown"
    assert details["parse_error_code"] == "unsupported_shell"
    assert details["parse_error_position"] is None


def test_exec_run_workdir(tmp_path):
    ctx = _ctx(tmp_path)
    result = _h_exec_run({"command": "pwd", "workdir": "sub1/sub2"}, ctx)
    assert result["status"] == "error"


def test_exec_run_workspace_basename_workdir_resolves_to_workspace_root(tmp_path):
    runner = _RecordingSandboxRunner(ExecResult(returncode=0, stdout="ok\n", stderr=""))
    ctx = _ctx(tmp_path, sandbox_runner=runner)

    result = _h_exec_run({"command": "printf 'ok\\n'", "workdir": "workspace"}, ctx)

    assert result["status"] == "ok"
    assert runner.calls
    assert runner.calls[0][0].cwd == str(ctx.workspace.resolve())


def test_exec_run_allowed_path_basename_workdir_resolves_to_allowed_root(tmp_path):
    runner = _RecordingSandboxRunner(ExecResult(returncode=0, stdout="ok\n", stderr=""))
    ctx = _ctx(tmp_path, sandbox_runner=runner)
    scratch_workspace = tmp_path / "runs" / "research-project-123"
    scratch_workspace.mkdir(parents=True)
    ctx.policy.raw["paths"]["read_allow"].append(str(scratch_workspace))
    ctx.policy.raw["paths"]["write_allow"].append(str(scratch_workspace))

    result = _h_exec_run(
        {"command": "printf 'ok\\n'", "workdir": "research-project-123"},
        ctx,
    )

    assert result["status"] == "ok"
    assert runner.calls
    assert runner.calls[0][0].cwd == str(scratch_workspace.resolve())


def test_exec_run_dot_workdir_honors_tool_workspace_env(tmp_path):
    runner = _RecordingSandboxRunner(ExecResult(returncode=0, stdout="ok\n", stderr=""))
    scratch_workspace = tmp_path / "scratch-project"
    scratch_workspace.mkdir(parents=True)
    ctx = _ctx(
        tmp_path,
        sandbox_runner=runner,
        env={"OPENMINION_WORKSPACE_ROOT": str(scratch_workspace)},
    )
    ctx.policy.raw["paths"]["read_allow"].append(str(scratch_workspace))
    ctx.policy.raw["paths"]["write_allow"].append(str(scratch_workspace))

    result = _h_exec_run({"command": "printf 'ok\\n'", "workdir": "."}, ctx)

    assert result["status"] == "ok"
    assert runner.calls
    assert runner.calls[0][0].cwd == str(scratch_workspace.resolve())


def test_exec_uses_shared_emit_family_event_helper(tmp_path) -> None:
    from unittest.mock import patch

    ctx = _ctx(tmp_path)
    with patch("openminion.tools.exec.plugin.emit_family_event") as mock_emit:
        _h_exec_run({"command": "echo hello"}, ctx)

    assert mock_emit.called, "emit_family_event must be called by exec plugin"
    emitted_events = [call.kwargs.get("event") for call in mock_emit.call_args_list]
    assert any(ev and "tool.requested" in ev for ev in emitted_events)


def test_exec_externalized_output_prefers_canonical_ref_when_cas_available(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.artifactctl = _RecordingArtifactCtl()
    ctx.session_id = "sess-exec"
    ctx.trace_id = "trace-exec"
    ctx.tool_name = "exec.run"

    artifact = _artifactize_output(
        ctx,
        session_id="sess-exec",
        stream="stdout",
        payload=b"x" * 32,
    )

    assert artifact is not None
    assert artifact["ref"] == "artifact://sha256/" + ("d" * 64)
    assert artifact["meta"]["canonical_ref"] == "artifact://sha256/" + ("d" * 64)


def test_exec_externalized_output_falls_back_to_local_log_when_cas_fails(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.artifactctl = _FailingArtifactCtl()
    ctx.session_id = "sess-exec"
    ctx.trace_id = "trace-exec"
    ctx.tool_name = "exec.run"

    artifact = _artifactize_output(
        ctx,
        session_id="sess-exec",
        stream="stderr",
        payload=b"x" * 32,
    )

    assert artifact is not None
    assert artifact["ref"].startswith("artifacts/exec/")
    assert artifact["meta"]["canonical_ref"] == ""
