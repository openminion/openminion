from __future__ import annotations

from pathlib import Path

from openminion.api.runtime import APIRuntime
from openminion.api.operations.tools import execute_tool_run
from openminion.base.config import OpenMinionConfig, save_config
from tests._csc_fixtures import _csc_install_default_agent


def _write_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.json"
    config = OpenMinionConfig()
    _csc_install_default_agent(config, provider="echo")
    config.runtime.log_level = "ERROR"
    config.storage.path = str(tmp_path / "state" / "runtime.db")
    save_config(config, str(config_path))
    return config_path


def test_api_runtime_bootstraps_authored_tool_surfaces(tmp_path: Path) -> None:
    runtime = APIRuntime.from_config_path(str(_write_config(tmp_path)))
    try:
        assert getattr(runtime, "authored_tools", None) is not None
        names = set(runtime.tools.list().keys())
        assert "tool.author" in names
        assert "tool.inspect" in names
        assert "tool.register" in names
        assert "tool.get" in names
    finally:
        runtime.close()


def test_api_tools_route_has_authored_tools_api_bound(tmp_path: Path) -> None:
    runtime = APIRuntime.from_config_path(str(_write_config(tmp_path)))
    try:
        status, payload, _session_id = execute_tool_run(
            runtime=runtime,
            tool_name="tool.author",
            arguments={
                "name": "adder",
                "description": "Add two integers",
                "source_code": "def adder(x, y):\n    return x + y\n",
                "unit_tests_source": "def test_add():\n    assert True\n",
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "required": ["x", "y"],
                },
                "returns_schema": {"type": "integer"},
                "requirements": [],
                "dependencies": [],
                "proposed_scope_tier": "POWER_USER",
            },
            request_id="req-1",
            channel="console",
            target="tester",
            requested_session_id="sess-1",
        )
        assert int(status) == 200
        assert payload["ok"] is True
        assert payload["tool"]["data"]["status"] == "drafted"
    finally:
        runtime.close()
