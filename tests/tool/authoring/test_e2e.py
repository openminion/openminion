from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
import tempfile

from openminion.api.operations.tools import execute_tool_run
from openminion.api.runtime import APIRuntime
from openminion.base.config import OpenMinionConfig, save_config
from openminion.cli.commands.tool_control import run_toolctl
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool import ToolExecutionContext
from openminion.tools.tool_authoring.runner import execute_tool_file
from tests._csc_fixtures import _csc_install_default_agent


class _EndToEndSandboxRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []
        self.test_runs = 0
        self.invoke_runs = 0

    def run_exec(self, spec, sandbox):
        self.calls.append((spec, sandbox))
        command = list(getattr(spec, "cmd", []) or [])
        if "-c" in command:
            self.test_runs += 1
            return Namespace(
                returncode=0,
                stdout="1 passed in 0.01s\n",
                stderr="",
                truncated=False,
                timed_out=False,
            )
        self.invoke_runs += 1
        tool_file = str(command[command.index("--tool-file") + 1])
        entry_function = str(command[command.index("--entry-function") + 1])
        raw_args = str(command[command.index("--args-json") + 1])
        payload = json.loads(raw_args or "{}")
        result = execute_tool_file(
            tool_file=tool_file,
            entry_function=entry_function,
            arguments=dict(payload),
        )
        return Namespace(
            returncode=0,
            stdout=json.dumps(result),
            stderr="",
            truncated=False,
            timed_out=False,
        )

    def close(self) -> None:
        return None


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.runtime.log_level = "ERROR"
    config.storage.path = str(tmp_path / "state" / "runtime.db")
    save_config(config, str(config_path))
    return config_path


def _tool_payload(source_code: str) -> dict[str, object]:
    return {
        "name": "adder",
        "description": "Add two integers",
        "source_code": source_code,
        "unit_tests_source": "def test_add():\n    assert True\n",
        "args_schema": {
            "type": "object",
            "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
            "required": ["x", "y"],
        },
        "returns_schema": {"type": "integer"},
        "requirements": [],
        "dependencies": [],
        "proposed_scope_tier": "POWER_USER",
    }


def _run_tool(
    runtime: APIRuntime, *, tool_name: str, arguments: dict[str, object]
) -> dict[str, object]:
    status, payload, _session_id = execute_tool_run(
        runtime=runtime,
        tool_name=tool_name,
        arguments=arguments,
        request_id=f"req-{tool_name}",
        channel="console",
        target="aat-e2e",
        requested_session_id="aat-e2e",
    )
    assert int(status) == 200
    assert payload["ok"] is True
    return payload["tool"]["data"]


def test_authored_tool_pipeline_end_to_end(monkeypatch) -> None:
    runner = _EndToEndSandboxRunner()
    monkeypatch.setattr(
        "openminion.api.core.infrastructure.build_daytona_runner",
        lambda **kwargs: runner,
    )

    with tempfile.TemporaryDirectory() as tmp:
        config_path = _write_config(Path(tmp))
        runtime = APIRuntime.from_config_path(
            str(config_path),
            home_root=str(Path(tmp) / "home"),
            data_root=str(Path(tmp) / "data"),
        )
        try:
            drafted_v1 = _run_tool(
                runtime,
                tool_name="tool.author",
                arguments=_tool_payload("def adder(x, y):\n    return x + y\n"),
            )
            inspected_v1 = _run_tool(
                runtime,
                tool_name="tool.inspect",
                arguments={"draft_id": drafted_v1["draft_id"], "run_tests": True},
            )
            assert inspected_v1["recommend_register"] is True
            registered_v1 = _run_tool(
                runtime,
                tool_name="tool.register",
                arguments={"draft_id": drafted_v1["draft_id"]},
            )
            assert registered_v1["tool_name"] == "authored.adder@v1"
            assert registered_v1["policy_grant_id"]
            assert "authored.adder@v1" in runtime.tools.list()

            batch = runtime.tools.execute_calls(
                [
                    ProviderToolCall(
                        name="authored.adder@v1",
                        arguments={"x": 1, "y": 2},
                        id="invoke-v1",
                        source="test",
                    )
                ],
                context=ToolExecutionContext(
                    channel="console",
                    target="aat-e2e",
                    session_id="aat-e2e",
                    authored_tools_api=runtime.authored_tools,
                    sandbox_runner=runtime.sandbox_runner,
                ),
            )
            assert batch.results[0].ok is True
            assert batch.results[0].data["result"] == 3
            assert runner.invoke_runs == 1

            for _ in range(19):
                invoked = runtime.authored_tools.invoke(
                    "authored.adder@v1", {"x": 2, "y": 3}
                )
                assert invoked["ok"] is True
            promoted_row_pre = runtime.authored_tools.get_authored_tool(
                "authored.adder@v1"
            )
            assert promoted_row_pre is not None
            assert promoted_row_pre.success_count == 20

            drafted_v2 = _run_tool(
                runtime,
                tool_name="tool.author",
                arguments=_tool_payload("def adder(x, y):\n    return (x + y) + 1\n"),
            )
            _run_tool(
                runtime,
                tool_name="tool.inspect",
                arguments={"draft_id": drafted_v2["draft_id"], "run_tests": True},
            )
            registered_v2 = _run_tool(
                runtime,
                tool_name="tool.register",
                arguments={"draft_id": drafted_v2["draft_id"]},
            )
            assert registered_v2["tool_name"] == "authored.adder@v2"

            test_runs_before_promote = runner.test_runs
            assert (
                run_toolctl(
                    Namespace(
                        toolctl_command="promote",
                        tool_name="authored.adder@v1",
                        force=False,
                    ),
                    runtime,
                )
                == 0
            )
            assert (
                runtime.authored_tools.get_authored_tool("authored.adder@v1").tier
                == "trusted"
            )
            assert runner.test_runs == test_runs_before_promote + 1

            assert (
                run_toolctl(
                    Namespace(
                        toolctl_command="remove",
                        tool_name="authored.adder@v2",
                        reason="cleanup",
                    ),
                    runtime,
                )
                == 0
            )
            removed_row = runtime.authored_tools.get_authored_tool("authored.adder@v2")
            assert removed_row is not None
            assert removed_row.removed_at is not None
        finally:
            runtime.close()

        runtime_rehydrated = APIRuntime.from_config_path(
            str(config_path),
            home_root=str(Path(tmp) / "home"),
            data_root=str(Path(tmp) / "data"),
        )
        try:
            names = set(runtime_rehydrated.tools.list().keys())
            assert "authored.adder@v1" in names
            assert "authored.adder@v2" not in names
        finally:
            runtime_rehydrated.close()
