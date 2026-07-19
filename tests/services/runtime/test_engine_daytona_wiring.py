from __future__ import annotations

from pathlib import Path
import tempfile

from openminion.api.runtime import APIRuntime
from openminion.base.config import OpenMinionConfig, save_config
from openminion.base.runtime.sandbox import ExecSpec
from openminion.services.runtime.bootstrap import build_daytona_runner
from openminion.modules.runtime.sandboxes.daytona import DaytonaRunner
from openminion.services.runtime.engine import (
    PolicyDecision,
    RuntimeContext,
    RuntimeEngine,
    ToolCall,
)
from tests._csc_fixtures import _csc_install_default_agent


class _AllowAllPolicy:
    contract_version = "v1"

    def evaluate(self, tool_call: ToolCall, ctx: RuntimeContext) -> PolicyDecision:
        return PolicyDecision(outcome="allow", policy_request_id="pr-daytona")


class _FakeRunnerClient:
    def __init__(self) -> None:
        self.connected = False
        self.executed: list[dict[str, object]] = []

    def open(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.connected = False

    def create_workspace(self, *, name: str, image: str | None = None, metadata=None):
        return type(
            "Workspace",
            (),
            {
                "workspace_id": "ws-1",
                "name": name,
                "image": image or "default",
                "metadata": dict(metadata or {}),
            },
        )()

    def destroy_workspace(self, workspace_id: str) -> None:
        return None

    def execute_command(
        self,
        *,
        workspace_id: str,
        command: list[str],
        cwd: str | None = None,
        env=None,
        env_allowlist=None,
        timeout_s: float | None = None,
        max_output_bytes: int | None = None,
    ):
        self.executed.append(
            {
                "workspace_id": workspace_id,
                "command": list(command),
                "cwd": cwd,
                "env": dict(env or {}),
            }
        )
        return type(
            "CommandResult",
            (),
            {
                "workspace_id": workspace_id,
                "returncode": 0,
                "stdout": "ok",
                "stderr": "",
                "truncated": False,
                "timed_out": False,
            },
        )()


def _write_echo_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.log_level = "ERROR"
    config.runtime.env["OPENMINION_DAYTONA_ENDPOINT"] = "https://daytona.example"
    _csc_install_default_agent(config, provider="echo")
    config.storage.path = str(tmp_path / "state" / "runtime.db")
    save_config(config, str(config_path))
    return config_path


def test_build_daytona_runner_uses_runtime_env_config() -> None:
    config = OpenMinionConfig()
    config.runtime.env["OPENMINION_DAYTONA_ENDPOINT"] = "https://daytona.example"

    runner = build_daytona_runner(config=config)

    assert isinstance(runner, DaytonaRunner)
    assert runner is not None
    assert runner._client.config.endpoint == "https://daytona.example"  # noqa: SLF001


def test_api_runtime_carries_daytona_runner_when_endpoint_configured() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config_path = _write_echo_config(Path(tmp))
        runtime = APIRuntime.from_config_path(str(config_path))
        try:
            assert isinstance(runtime.sandbox_runner, DaytonaRunner)
        finally:
            runtime.close()


def test_runtime_engine_executes_through_daytona_runner() -> None:
    client = _FakeRunnerClient()
    runner = DaytonaRunner(client=client)
    engine = RuntimeEngine(runner=runner, policy=_AllowAllPolicy())
    ctx = RuntimeContext(
        trace_id="tr-1",
        agent_id="agent-1",
        session_id="sess-1",
        run_id="run-1",
        workspace_root="/workspace",
        tool_caps={
            "cmd_allowlist": ["echo"],
            "write_allow": ["/workspace"],
            "read_allow": ["/workspace"],
        },
    )
    tool_call = ToolCall(
        tool_call_id="tc-1",
        name="exec",
        kind="exec",
        spec=ExecSpec(cmd=["echo", "hello"]),
    )

    result = engine.execute_tool_call(tool_call, ctx)

    assert result.outcome == "completed"
    assert client.executed[0]["command"] == ["echo", "hello"]
