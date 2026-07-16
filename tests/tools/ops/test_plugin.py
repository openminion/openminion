from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.modules.tool.framework import derive_manifest, derive_tool_specs
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.ops import OPS_FAMILY, REGISTRAR
from openminion.tools.ops.args import ProfileArgs
from openminion.tools.ops.interfaces import (
    ALL_OPS_TOOLS,
    TOOL_OPS_HOST_SNAPSHOT,
    TOOL_OPS_JOB_CANCEL,
)


def test_ops_registrar_registers_exact_tool_family_surface() -> None:
    registry = ToolRegistry()

    REGISTRAR.register(registry)

    tools = registry.list()
    assert tuple(sorted(tools)) == tuple(sorted(ALL_OPS_TOOLS))
    assert all(tool.dangerous is False for tool in tools.values())
    assert tools[TOOL_OPS_JOB_CANCEL].capabilities == (
        "operation_control",
        "ops",
        "evidence",
    )
    assert all(
        tool.capabilities[0] == "read_only"
        for name, tool in tools.items()
        if name != TOOL_OPS_JOB_CANCEL
    )


def test_ops_registrar_manifest_matches_registered_tool_surface() -> None:
    manifest = REGISTRAR.get_manifest(None)

    assert manifest.module_id == "ops"
    assert len(manifest.model_tools) == len(ALL_OPS_TOOLS)
    assert (
        tuple(binding.runtime_candidates[0] for binding in manifest.runtime_bindings)
        == ALL_OPS_TOOLS
    )
    assert all(
        len(binding.runtime_candidates) == 1 for binding in manifest.runtime_bindings
    )


def test_ops_family_is_the_single_registration_and_manifest_owner() -> None:
    manifest = derive_manifest(OPS_FAMILY)
    specs = derive_tool_specs(OPS_FAMILY)

    assert tuple(tool.name for tool in specs) == ALL_OPS_TOOLS
    assert manifest == REGISTRAR.get_manifest(None)
    assert all(
        tool.description and not tool.description.startswith("Ops tool:")
        for tool in manifest.model_tools
    )


def test_ops_plugin_records_concrete_tool_id_in_evidence() -> None:
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    ctx = SimpleNamespace(extras={"session_id": "ops-plugin-test"})

    result = registry.get(TOOL_OPS_HOST_SNAPSHOT).handler({"target_id": "local"}, ctx)

    assert result["ok"] is True
    assert result["data"]["session_id"] == "ops-plugin-test"
    assert result["data"]["tool_id"] == TOOL_OPS_HOST_SNAPSHOT


@pytest.mark.parametrize("field", ["command", "argv", "executable", "shell"])
def test_command_observe_rejects_free_form_execution_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProfileArgs.model_validate(
            {"target_id": "local", "profile_id": "disk.usage", field: "rm -rf /"}
        )
