from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
import sys

import pytest

from openminion.base.runtime.sandbox import (
    ExecSpec,
    ExecutionSandboxSpec,
    FsWriteSpec,
)
from openminion.services.runtime.daytona.client import (
    DaytonaClientError,
    DaytonaCommandResult,
    DaytonaWorkspace,
)
from openminion.services.runtime.daytona.runner import DaytonaRunner


@dataclass
class _FakeDaytonaClient:
    connected: bool = False
    open_calls: int = 0
    created: list[dict[str, Any]] = field(default_factory=list)
    destroyed: list[str] = field(default_factory=list)
    executed: list[dict[str, Any]] = field(default_factory=list)

    def open(self) -> None:
        self.open_calls += 1
        self.connected = True

    def create_workspace(
        self,
        *,
        name: str,
        image: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> DaytonaWorkspace:
        payload = {
            "name": name,
            "image": image,
            "metadata": dict(metadata or {}),
        }
        self.created.append(payload)
        return DaytonaWorkspace(
            workspace_id=f"ws-{len(self.created)}",
            name=name,
            image=str(image or "default"),
            metadata=dict(metadata or {}),
        )

    def destroy_workspace(self, workspace_id: str) -> None:
        self.destroyed.append(workspace_id)

    def execute_command(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        env_allowlist: list[str] | tuple[str, ...] | None = None,
        timeout_s: float | None = None,
        max_output_bytes: int | None = None,
    ) -> DaytonaCommandResult:
        payload = {
            "workspace_id": workspace_id,
            "command": list(command),
            "cwd": cwd,
            "env": dict(env or {}),
            "env_allowlist": list(env_allowlist or []),
            "timeout_s": timeout_s,
            "max_output_bytes": max_output_bytes,
        }
        self.executed.append(payload)
        metadata = self.created[-1]["metadata"] if self.created else {}
        if metadata.get("net_mode") == "deny" and command and command[0] == "curl":
            raise DaytonaClientError(
                code="SANDBOX_NETWORK_DENIED",
                message="network denied",
            )
        if timeout_s is not None and timeout_s <= 0.1:
            raise DaytonaClientError(
                code="SANDBOX_RESOURCE_LIMIT",
                message="timeout exceeded",
            )
        return DaytonaCommandResult(
            workspace_id=workspace_id,
            returncode=0,
            stdout="hello world",
            stderr="",
            truncated=False,
            timed_out=False,
        )


def _sandbox(tmp_path, **overrides: Any) -> ExecutionSandboxSpec:
    ws = str(tmp_path)
    defaults = dict(
        workspace_root=ws,
        read_allow=[ws],
        write_allow=[ws],
        delete_allow=[ws],
        cmd_allowlist=["echo", "curl", "python3.11", "python", sys.executable],
        env_allowlist=["PATH"],
        timeout_s=10.0,
        max_output_bytes=4096,
        net_mode="deny",
    )
    defaults.update(overrides)
    return ExecutionSandboxSpec(**defaults)


def test_daytona_runner_exec_happy_path(tmp_path) -> None:
    client = _FakeDaytonaClient()
    runner = DaytonaRunner(client=client)
    sandbox = _sandbox(tmp_path)

    result = runner.run_exec(ExecSpec(cmd=["echo", "hello"]), sandbox)

    assert result.returncode == 0
    assert result.stdout == "hello world"
    assert client.open_calls == 1
    assert len(client.created) == 1
    assert client.destroyed == ["ws-1"]


def test_daytona_runner_fs_write_outside_allowlist_denied(tmp_path) -> None:
    client = _FakeDaytonaClient()
    runner = DaytonaRunner(client=client)
    sandbox = _sandbox(tmp_path, write_allow=[str(tmp_path)])

    with pytest.raises(PermissionError, match="outside allowed roots"):
        runner.fs_write(FsWriteSpec(path="/tmp/evil.txt", content="x"), sandbox)


def test_daytona_runner_strips_non_allowlisted_openminion_env(tmp_path) -> None:
    client = _FakeDaytonaClient()
    runner = DaytonaRunner(client=client)
    sandbox = _sandbox(tmp_path, env_allowlist=["PATH"])

    runner.run_exec(
        ExecSpec(
            cmd=["echo", "hello"],
            env={"PATH": "/bin", "OPENMINION_API_KEY": "secret"},
        ),
        sandbox,
    )

    assert client.executed[0]["env"] == {"PATH": "/bin"}


def test_daytona_runner_blocks_network_egress_when_denied(tmp_path) -> None:
    client = _FakeDaytonaClient()
    runner = DaytonaRunner(client=client)
    sandbox = _sandbox(tmp_path, net_mode="deny")

    with pytest.raises(DaytonaClientError) as exc:
        runner.run_exec(ExecSpec(cmd=["curl", "https://example.com"]), sandbox)

    assert exc.value.code == "SANDBOX_NETWORK_DENIED"
    assert client.destroyed == ["ws-1"]


def test_daytona_runner_maps_timeout_to_timed_out_result(tmp_path) -> None:
    client = _FakeDaytonaClient()
    runner = DaytonaRunner(client=client)
    sandbox = _sandbox(tmp_path, timeout_s=0.1)

    result = runner.run_exec(ExecSpec(cmd=["python", "-c", "print('x')"]), sandbox)

    assert result.returncode == -1
    assert result.timed_out is True
    assert "timeout" in result.stderr


def test_daytona_runner_passes_resource_and_network_metadata(tmp_path) -> None:
    client = _FakeDaytonaClient()
    runner = DaytonaRunner(client=client)
    sandbox = _sandbox(
        tmp_path,
        net_mode="allow",
        allowed_domains=["example.com"],
        address_space_bytes=1024,
        cpu_seconds=5.0,
        session_mode="foreground",
    )

    runner.run_exec(ExecSpec(cmd=["echo", "hello"]), sandbox)

    metadata = client.created[0]["metadata"]
    assert metadata["net_mode"] == "allow"
    assert metadata["allowed_domains"] == ["example.com"]
    assert metadata["address_space_bytes"] == 1024
    assert metadata["cpu_seconds"] == 5.0
    assert metadata["session_mode"] == "foreground"
