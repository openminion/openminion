from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.policy import Policy
from openminion.services.runtime.daytona.client import (
    DaytonaConfig,
    DaytonaTransportError,
)
from openminion.services.runtime.daytona.runner import DaytonaRunner
from openminion.tools.exec.constants import EXEC_ARTIFACT_THRESHOLD_BYTES
from openminion.tools.exec.plugin import _h_exec_run


def _ctx(tmp_path: Path, *, sandbox_runner=None) -> RuntimeContext:
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
                    "printf",
                    "sleep",
                    "echo",
                    "cat",
                    "curl",
                ],
                "deny_exact": [],
                "deny_regex": [],
            },
            "env": {"allow_keys": ["PATH", "HOME"], "deny_keys_regex": []},
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="WRITE_SAFE",
        confirm=False,
        sandbox_runner=sandbox_runner,
    )


@dataclass
class _FakeSandboxTransport:
    command_requests: list[dict[str, Any]] = field(default_factory=list)
    command_response: Mapping[str, Any] = field(
        default_factory=lambda: {
            "workspace_id": "ws-1",
            "returncode": 0,
            "stdout": "sandbox hello\n",
            "stderr": "",
        }
    )
    command_error: Exception | None = None
    workspace_metadata: dict[str, Any] = field(default_factory=dict)

    def open(self, config, *, api_key: str) -> None:
        del config, api_key

    def close(self) -> None:
        return None

    def create_workspace(
        self,
        *,
        name: str,
        image: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        self.workspace_metadata = dict(metadata or {})
        return {
            "workspace_id": "ws-1",
            "name": name,
            "image": image,
            "metadata": dict(metadata or {}),
        }

    def destroy_workspace(self, workspace_id: str) -> None:
        del workspace_id

    def execute_command(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None,
        env: Mapping[str, str],
        timeout_s: float,
        max_output_bytes: int,
    ) -> Mapping[str, Any]:
        self.command_requests.append(
            {
                "workspace_id": workspace_id,
                "command": list(command),
                "cwd": cwd,
                "env": dict(env),
                "timeout_s": timeout_s,
                "max_output_bytes": max_output_bytes,
                "workspace_metadata": dict(self.workspace_metadata),
            }
        )
        if self.command_error is not None:
            raise self.command_error
        return self.command_response

    def start_session(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None,
        env: Mapping[str, str],
        timeout_s: float,
        max_output_bytes: int,
        use_pty: bool,
    ) -> Mapping[str, Any]:
        del workspace_id, command, cwd, env, timeout_s, max_output_bytes, use_pty
        return {"workspace_id": "ws-1", "session_id": "remote-1"}

    def poll_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        max_output_bytes: int,
    ) -> Mapping[str, Any]:
        del workspace_id, session_id, max_output_bytes
        return {
            "workspace_id": "ws-1",
            "session_id": "remote-1",
            "running": False,
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
        }

    def send_session_input(
        self,
        *,
        workspace_id: str,
        session_id: str,
        payload: bytes,
    ) -> Mapping[str, Any] | None:
        del workspace_id, session_id, payload
        return None

    def terminate_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        signal_name: str,
    ) -> Mapping[str, Any] | None:
        del workspace_id, session_id, signal_name
        return {
            "workspace_id": "ws-1",
            "session_id": "remote-1",
            "running": False,
            "exit_code": -15,
            "stdout": "",
            "stderr": "",
            "killed": True,
        }


def _runner(transport: _FakeSandboxTransport) -> DaytonaRunner:
    from openminion.services.runtime.daytona.client import DaytonaClient

    return DaytonaRunner(
        client=DaytonaClient(
            config=DaytonaConfig(endpoint="https://daytona.example", api_key="secret"),
            transport=transport,
        )
    )


def test_sandbox_default_path_executes_through_daytona_runner(tmp_path: Path) -> None:
    transport = _FakeSandboxTransport()
    ctx = _ctx(tmp_path, sandbox_runner=_runner(transport))

    result = _h_exec_run({"command": "printf 'sandbox hello\\n'"}, ctx)

    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert "sandbox hello" in str(result.get("stdout_preview") or "")
    assert (
        transport.command_requests[0]["workspace_metadata"]["session_mode"]
        == "foreground"
    )


def test_sandbox_e2e_unsandboxed_denied_by_default(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    result = _h_exec_run(
        {"command": "echo hi", "host": "gateway", "security": "full", "ask": "off"},
        ctx,
    )

    assert result["status"] == "denied"
    assert result["error"]["code"] == "UNSANDBOXED_EXEC_DISABLED"


def test_sandbox_e2e_network_deny_surfaces_typed_error(tmp_path: Path) -> None:
    transport = _FakeSandboxTransport(
        command_error=DaytonaTransportError(
            code="NETWORK_DENIED",
            message="egress denied",
        )
    )
    ctx = _ctx(tmp_path, sandbox_runner=_runner(transport))

    result = _h_exec_run({"command": "curl https://example.com"}, ctx)

    assert result["status"] == "error"
    assert result["error"]["code"] == "SANDBOX_NETWORK_DENIED"


def test_sandbox_e2e_resource_limit_surfaces_timeout_contract(tmp_path: Path) -> None:
    transport = _FakeSandboxTransport(
        command_error=DaytonaTransportError(
            code="TIMEOUT",
            message="deadline exceeded",
        )
    )
    ctx = _ctx(tmp_path, sandbox_runner=_runner(transport))

    result = _h_exec_run({"command": "sleep 10", "timeout_s": 1}, ctx)

    assert result["status"] == "error" or result["status"] == "timeout"
    assert result["error"]["code"] == "SANDBOX_RESOURCE_LIMIT"


def test_sandbox_e2e_unavailable_runner_surfaces_typed_error(tmp_path: Path) -> None:
    transport = _FakeSandboxTransport(
        command_error=DaytonaTransportError(
            code="UNAVAILABLE",
            message="runner offline",
        )
    )
    ctx = _ctx(tmp_path, sandbox_runner=_runner(transport))

    result = _h_exec_run({"command": "printf 'hello\\n'"}, ctx)

    assert result["status"] == "error"
    assert result["error"]["code"] == "SANDBOX_UNAVAILABLE"


def test_sandbox_e2e_large_output_preserves_artifact_spillover(tmp_path: Path) -> None:
    payload = "x" * (EXEC_ARTIFACT_THRESHOLD_BYTES + 128)
    transport = _FakeSandboxTransport(
        command_response={
            "workspace_id": "ws-1",
            "returncode": 0,
            "stdout": payload,
            "stderr": "",
        }
    )
    ctx = _ctx(tmp_path, sandbox_runner=_runner(transport))

    result = _h_exec_run({"command": "printf 'large'"}, ctx)

    assert result["status"] == "ok"
    assert result["stdout_artifact"] is not None
    assert result["stdout_preview"] is not None
