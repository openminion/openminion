from types import SimpleNamespace

from openminion.modules.brain.schemas import ToolCommand
from openminion.modules.brain.tools.executor.arguments import sanitize_tool_command_args
from openminion.modules.tool import ToolRegistry
from openminion.tools.exec.plugin import register as register_exec_tools


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
