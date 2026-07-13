from __future__ import annotations

import pytest
from pydantic import ValidationError

from openminion.modules.system_operations.manifest import READ_ONLY_TOOLS
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.ops.interfaces import TOOL_OPS_JOB_CANCEL
from openminion.tools.ops.plugin import register
from openminion.tools.ops.schemas import ProfileArgs


def test_ops_plugin_registers_exact_read_only_pack_surface() -> None:
    registry = ToolRegistry()

    register(registry)

    tools = registry.list()
    assert tuple(sorted(tools)) == tuple(sorted(READ_ONLY_TOOLS))
    assert all(tool.dangerous is False for tool in tools.values())
    assert tools[TOOL_OPS_JOB_CANCEL].capabilities == (
        "operation_control",
        "system_operations",
        "evidence",
    )
    assert all(
        tool.capabilities[0] == "read_only"
        for name, tool in tools.items()
        if name != TOOL_OPS_JOB_CANCEL
    )


@pytest.mark.parametrize("field", ["command", "argv", "executable", "shell"])
def test_command_observe_rejects_free_form_execution_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProfileArgs.model_validate(
            {"target_id": "local", "profile_id": "disk.usage", field: "rm -rf /"}
        )
