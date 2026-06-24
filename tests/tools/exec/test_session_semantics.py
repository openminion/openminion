from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Mapping

import pytest

from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime.policy import Policy
from openminion.services.runtime.daytona.runner import DaytonaRunner
from openminion.tools.exec.plugin import (
    _h_exec_run,
    _h_process_clear,
    _h_process_kill,
    _h_process_paste,
    _h_process_poll,
    _h_process_send_keys,
    _h_process_submit,
)
from openminion.tools.exec.process import PROCESS_MANAGER


@pytest.fixture(autouse=True)
def _cleanup_sessions() -> None:
    PROCESS_MANAGER._reset_for_tests()
    try:
        yield
    finally:
        PROCESS_MANAGER._reset_for_tests()


def _ctx(tmp_path: Path, *, sandbox_runner: DaytonaRunner) -> RuntimeContext:
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
class _FakeSessionState:
    command: list[str]
    use_pty: bool
    running: bool = True
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    poll_count: int = 0
    killed: bool = False


@dataclass
class _FakeSessionTransport:
    workspaces: list[dict[str, Any]] = field(default_factory=list)
    destroyed: list[str] = field(default_factory=list)
    sessions: dict[str, _FakeSessionState] = field(default_factory=dict)
    closed: int = 0

    def open(self, config, *, api_key: str) -> None:
        del config, api_key

    def close(self) -> None:
        self.closed += 1

    def create_workspace(
        self,
        *,
        name: str,
        image: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        workspace_id = f"ws-{len(self.workspaces) + 1}"
        self.workspaces.append(
            {
                "workspace_id": workspace_id,
                "name": name,
                "image": image,
                "metadata": dict(metadata or {}),
            }
        )
        return {
            "workspace_id": workspace_id,
            "name": name,
            "image": image,
            "metadata": dict(metadata or {}),
        }

    def destroy_workspace(self, workspace_id: str) -> None:
        self.destroyed.append(workspace_id)

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
        del workspace_id, command, cwd, env, timeout_s, max_output_bytes
        return {
            "workspace_id": "ws-one-shot",
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
        }

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
        del cwd, env, timeout_s, max_output_bytes
        session_id = f"remote-{len(self.sessions) + 1}"
        self.sessions[session_id] = _FakeSessionState(
            command=list(command),
            use_pty=use_pty,
        )
        return {"workspace_id": workspace_id, "session_id": session_id}

    def poll_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        max_output_bytes: int,
    ) -> Mapping[str, Any]:
        del max_output_bytes
        state = self.sessions[session_id]
        state.poll_count += 1
        command_text = " ".join(state.command)
        if (
            state.running
            and "sleep 0.2; echo done" in command_text
            and state.poll_count >= 3
        ):
            state.running = False
            state.exit_code = 0
            state.stdout = "done\n"
        return {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "running": state.running,
            "exit_code": state.exit_code,
            "stdout": state.stdout,
            "stderr": state.stderr,
            "killed": state.killed,
        }

    def send_session_input(
        self,
        *,
        workspace_id: str,
        session_id: str,
        payload: bytes,
    ) -> Mapping[str, Any] | None:
        del workspace_id
        state = self.sessions[session_id]
        text = payload.replace(b"\x1b[200~", b"").replace(b"\x1b[201~", b"")
        if text:
            state.stdout += text.decode("utf-8", errors="replace")
        if b"\x04" in payload:
            state.running = False
            state.exit_code = 0
        return None

    def terminate_session(
        self,
        *,
        workspace_id: str,
        session_id: str,
        signal_name: str,
    ) -> Mapping[str, Any] | None:
        del signal_name
        state = self.sessions[session_id]
        state.running = False
        state.killed = True
        state.exit_code = -15
        return {
            "workspace_id": workspace_id,
            "session_id": session_id,
            "running": False,
            "exit_code": -15,
            "stdout": state.stdout,
            "stderr": state.stderr,
            "killed": True,
        }


def _runner() -> DaytonaRunner:
    from openminion.services.runtime.daytona.client import DaytonaClient
    from openminion.services.runtime.daytona.config import DaytonaConfig

    return DaytonaRunner(
        client=DaytonaClient(
            config=DaytonaConfig(endpoint="https://daytona.example", api_key="secret"),
            transport=_FakeSessionTransport(),
        )
    )


def test_sandbox_background_session_polls_to_exit(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, sandbox_runner=_runner())

    started = _h_exec_run({"command": "sleep 0.2; echo done", "background": True}, ctx)

    assert started["status"] == "running"
    session_id = str(started["session_id"])
    assert session_id.startswith("execsandbox_")
    assert PROCESS_MANAGER.snapshot(session_id=session_id, agent_id="default") is None

    final = {}
    combined: list[str] = []
    for _ in range(20):
        polled = _h_process_poll({"session_id": session_id, "tail_lines": 50}, ctx)
        if polled.get("stdout_preview"):
            combined.append(str(polled["stdout_preview"]))
        if polled["status"] != "running":
            final = polled
            break
        time.sleep(0.02)

    assert final["status"] == "exited"
    assert final["exit_code"] == 0
    assert "done" in "\n".join(combined)


def test_sandbox_pty_session_accepts_input_and_submit(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, sandbox_runner=_runner())

    started = _h_exec_run({"command": "cat", "background": True, "pty": True}, ctx)

    assert started["status"] == "running"
    session_id = str(started["session_id"])

    pasted = _h_process_paste(
        {"session_id": session_id, "text": "hello_paste", "bracketed": False},
        ctx,
    )
    submitted = _h_process_submit({"session_id": session_id}, ctx)
    eof_sent = _h_process_send_keys({"session_id": session_id, "keys": ["C-D"]}, ctx)

    assert pasted["status"] == "ok"
    assert submitted["status"] == "ok"
    assert eof_sent["status"] == "ok"

    final = {}
    combined: list[str] = []
    for _ in range(20):
        polled = _h_process_poll({"session_id": session_id, "tail_lines": 50}, ctx)
        if polled.get("stdout_preview"):
            combined.append(str(polled["stdout_preview"]))
        if polled["status"] != "running":
            final = polled
            break
        time.sleep(0.02)

    assert final["status"] == "exited"
    assert "hello_paste" in "".join(combined)


def test_sandbox_kill_and_clear_flow(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, sandbox_runner=_runner())

    started = _h_exec_run({"command": "sleep 10", "background": True}, ctx)
    session_id = str(started["session_id"])

    killed = _h_process_kill({"session_id": session_id}, ctx)
    assert killed["status"] == "ok"

    polled = _h_process_poll({"session_id": session_id, "tail_lines": 10}, ctx)
    assert polled["status"] == "killed"

    cleared = _h_process_clear({"session_id": session_id}, ctx)
    assert cleared["status"] == "ok"

    missing = _h_process_poll({"session_id": session_id, "tail_lines": 10}, ctx)
    assert missing["status"] == "killed"
    assert missing["error"]["code"] == "NOT_FOUND"
