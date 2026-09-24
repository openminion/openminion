from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
import os
from pathlib import Path
import subprocess
import sys

import pytest

from openminion.base.runtime.sandbox import (
    ExecSpec,
    ExecutionSandboxSpec,
    FsDeleteSpec,
    FsWriteSpec,
    NetFetchSpec,
)
from openminion.modules.runtime.sandboxes.daytona import (
    DaytonaClientError,
    DaytonaCommandResult,
    DaytonaWorkspace,
)
from openminion.modules.runtime.sandboxes.daytona import DaytonaRunner
from openminion.modules.tool.authoring.runtime.tests import run_tool_tests


@dataclass
class _FakeDaytonaClient:
    connected: bool = False
    open_calls: int = 0
    created: list[dict[str, Any]] = field(default_factory=list)
    destroyed: list[str] = field(default_factory=list)
    executed: list[dict[str, Any]] = field(default_factory=list)
    remote_root: str = ""

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
        if not self.connected:
            raise DaytonaClientError(
                code="SANDBOX_UNAVAILABLE", message="Daytona is closed"
            )
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
            metadata=(
                {"root_dir": self.remote_root}
                if self.remote_root
                else dict(metadata or {})
            ),
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


def test_daytona_runner_maps_python_and_workspace_to_remote(tmp_path) -> None:
    client = _FakeDaytonaClient(remote_root="/home/daytona")
    runner = DaytonaRunner(client=client)

    runner.run_exec(
        ExecSpec(cmd=[sys.executable, "-c", "print('hello')"], cwd=str(tmp_path)),
        _sandbox(tmp_path),
    )

    assert client.executed[0]["command"][0] == "python3"
    assert client.executed[0]["cwd"] == "/home/daytona"


def test_daytona_runner_does_not_fall_back_to_host_operations(tmp_path) -> None:
    client = _FakeDaytonaClient()
    runner = DaytonaRunner(client=client)
    sandbox = _sandbox(tmp_path)
    written = tmp_path / "written.txt"
    existing = tmp_path / "existing.txt"
    existing.write_text("keep", encoding="utf-8")

    with pytest.raises(DaytonaClientError, match="filesystem writes are not supported"):
        runner.fs_write(FsWriteSpec(path=str(written), content="x"), sandbox)
    with pytest.raises(
        DaytonaClientError, match="filesystem deletes are not supported"
    ):
        runner.fs_delete(FsDeleteSpec(path=str(existing)), sandbox)
    with pytest.raises(DaytonaClientError, match="network fetches are not supported"):
        runner.net_fetch(NetFetchSpec(url="https://example.com"), sandbox)

    assert not written.exists()
    assert existing.read_text(encoding="utf-8") == "keep"
    assert not client.created


def test_daytona_runner_executes_self_contained_tool_tests_remotely(
    tmp_path, monkeypatch
) -> None:
    remote_root = tmp_path / "remote"
    remote_root.mkdir()
    client = _FakeDaytonaClient(remote_root=str(remote_root))

    def execute_command(**kwargs: Any) -> DaytonaCommandResult:
        process = subprocess.run(
            kwargs["command"],
            cwd=kwargs["cwd"],
            env={
                **os.environ,
                "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
                **kwargs["env"],
            },
            capture_output=True,
            text=True,
            timeout=kwargs["timeout_s"],
            check=False,
        )
        return DaytonaCommandResult(
            workspace_id=kwargs["workspace_id"],
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )

    monkeypatch.setattr(client, "execute_command", execute_command)
    result = run_tool_tests(
        source_code="def add(a, b):\n    return a + b\n",
        unit_tests_source="from tool_impl import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        entry_function="add",
        sandbox_runner=DaytonaRunner(client=client),
    )

    assert (result.ran, result.passed, result.failed) == (1, 1, 0)
    assert (remote_root / "tool_impl.py").exists()
    assert client.destroyed == ["ws-1"]


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
