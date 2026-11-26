from types import SimpleNamespace

from openminion.modules.brain.schemas import ToolCommand
from openminion.modules.brain.tools.executor.arguments import sanitize_tool_command_args
from openminion.modules.tool import ToolRegistry
from openminion.tools.exec.plugin import register as register_exec_tools
from openminion.tools.gws.plugin import register as register_gws_tools
from openminion.tools.gws.schemas import GwsCallArgs


def test_sanitizer_canonicalizes_registered_model_aliases_before_filtering() -> None:
    registry = ToolRegistry()
    register_exec_tools(registry)
    runner = SimpleNamespace(tool_api=SimpleNamespace(registry=registry))
    command = ToolCommand(
        title="compile",
        tool_name="exec.run",
        args={"command_line": "python -m py_compile example.py"},
    )

    sanitized, removed = sanitize_tool_command_args(runner, command=command)

    assert sanitized["command"] == "python -m py_compile example.py"
    assert "command_line" not in sanitized
    assert removed == []


def test_sanitizer_keeps_schema_alias_keys_of_registered_models() -> None:
    registry = ToolRegistry()
    register_gws_tools(registry)
    runner = SimpleNamespace(
        tool_api=SimpleNamespace(registry=registry),
        _collect_runtime_tool_schemas=lambda: [
            {"name": "gws.call", "parameters": GwsCallArgs.model_json_schema()}
        ],
    )
    command = ToolCommand(
        title="create",
        tool_name="gws.call",
        args={"service": "drive", "method": "create", "json": {"name": "a"}},
    )

    sanitized, removed = sanitize_tool_command_args(runner, command=command)

    assert sanitized["json"] == {"name": "a"}
    assert "json_payload" not in sanitized
    assert removed == []
