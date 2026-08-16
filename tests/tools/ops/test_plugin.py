from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.modules.tool.framework import derive_manifest, derive_tool_specs
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.ops import OPS_FAMILY, REGISTRAR, local_ops_service
from openminion.tools.ops.args import PortOwnerArgs, ProcessArgs, ProfileArgs
from openminion.tools.ops.interfaces import (
    ALL_OPS_TOOLS,
    TOOL_OPS_HOST_SNAPSHOT,
    TOOL_OPS_COMMAND_PLAN,
    TOOL_OPS_COMMAND_RUN,
    TOOL_OPS_JOB_CANCEL,
    TOOL_OPS_PROCESS_INSPECT,
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
        if name
        not in {TOOL_OPS_COMMAND_PLAN, TOOL_OPS_COMMAND_RUN, TOOL_OPS_JOB_CANCEL}
    )
    assert tools[TOOL_OPS_COMMAND_PLAN].capabilities[0] == "operation_plan"
    assert tools[TOOL_OPS_COMMAND_RUN].capabilities[0] == "operation_control"


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


def test_process_inspect_records_typed_local_evidence() -> None:
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    ctx = SimpleNamespace(extras={"session_id": "process-plugin-test"})

    result = registry.get(TOOL_OPS_PROCESS_INSPECT).handler(
        {"target_id": "local", "pid": os.getpid()}, ctx
    )

    assert result["ok"] is True
    assert result["data"]["profile_id"] == "process.inspect"
    assert result["data"]["tool_id"] == TOOL_OPS_PROCESS_INSPECT
    assert result["data"]["redacted_parameters"]["pid"] == str(os.getpid())


@pytest.mark.parametrize(
    ("args_model", "payload"),
    [
        (ProcessArgs, {"target_id": "local", "pid": 0}),
        (
            PortOwnerArgs,
            {"target_id": "local", "port": 8080, "protocol": "sctp"},
        ),
        (
            PortOwnerArgs,
            {"target_id": "local", "port": 8080, "command": "lsof"},
        ),
    ],
)
def test_process_and_port_args_refuse_invalid_or_free_form_input(
    args_model, payload: dict[str, object]
) -> None:
    with pytest.raises(ValidationError):
        args_model.model_validate(payload)


@pytest.mark.parametrize("field", ["command", "argv", "executable", "shell"])
def test_command_observe_rejects_free_form_execution_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProfileArgs.model_validate(
            {"target_id": "local", "profile_id": "disk.usage", field: "rm -rf /"}
        )


def test_command_tools_use_injected_service_and_require_confirmation() -> None:
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    service = local_ops_service()
    context = ToolExecutionContext(
        channel="cli",
        target="local",
        session_id="session-1",
        ops_service=service,
    )
    planned = execute_tool_spec_call(
        tool=registry.get(TOOL_OPS_COMMAND_PLAN),
        arguments={"target_id": "local", "argv": ["printf", "ready"]},
        context=context,
    )
    plan = planned.data

    denied = execute_tool_spec_call(
        tool=registry.get(TOOL_OPS_COMMAND_RUN),
        arguments={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"]},
        context=context,
    )
    context.confirm = True
    completed = execute_tool_spec_call(
        tool=registry.get(TOOL_OPS_COMMAND_RUN),
        arguments={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"]},
        context=context,
    )

    assert denied.ok is False
    assert "operator approval" in denied.error
    assert completed.ok is True
    assert completed.data["status"] == "succeeded"
