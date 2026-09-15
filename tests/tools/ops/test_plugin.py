from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.policy.models import PolicyConfig, PolicyControlError
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.tool.framework import derive_manifest, derive_tool_specs
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.tools.ops import OPS_FAMILY, REGISTRAR, local_ops_service
from openminion.tools.ops.args import PortOwnerArgs, ProcessArgs, ProfileArgs
from openminion.tools.ops.contracts import OperationRequest
from openminion.tools.ops.interfaces import (
    ALL_OPS_TOOLS,
    TOOL_OPS_HOST_SNAPSHOT,
    TOOL_OPS_COMMAND_PLAN,
    TOOL_OPS_COMMAND_RUN,
    TOOL_OPS_JOB_CANCEL,
    TOOL_OPS_JOB_INSPECT,
    TOOL_OPS_PROCESS_INSPECT,
    TOOL_OPS_TARGET_INSPECT,
)


class _Telemetry:
    def __init__(self) -> None:
        self.events = []

    def emit_module_operation(self, *args, **kwargs):
        self.events.append((args, kwargs))


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


def test_ops_transport_telemetry_is_structural_and_redacted() -> None:
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    telemetry = _Telemetry()
    context = ToolExecutionContext(
        channel="cli",
        target="local",
        session_id="ops-telemetry",
        telemetryctl=telemetry,
        ops_service=local_ops_service(),
    )

    result = registry.get(TOOL_OPS_HOST_SNAPSHOT).handler(
        {"target_id": "local"}, context
    )

    assert result["ok"] is True
    assert [event[0][3] for event in telemetry.events] == [
        "transport.dispatch",
        "transport.result",
    ]
    result_facts = telemetry.events[-1][1]["extra"]
    assert set(result_facts) == {
        "target_id",
        "target_revision",
        "transport_kind",
        "capability",
        "duration_ms",
        "timed_out",
        "truncated",
        "error_code",
        "provider_request_id_digest",
        "job_id",
        "plan_id",
        "attempt_phase",
        "cancel_requested",
        "cancel_status",
        "remote_outcome",
        "approval_id",
        "policy_grant_id",
        "policy_invocation_hash",
    }
    assert not {"argv", "stdout", "stderr", "content", "credential"} & set(result_facts)


def test_explicit_target_probe_emits_one_structural_event() -> None:
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    telemetry = _Telemetry()
    context = ToolExecutionContext(
        channel="cli",
        target="local",
        session_id="ops-probe",
        telemetryctl=telemetry,
        ops_service=local_ops_service(),
    )

    result = registry.get(TOOL_OPS_TARGET_INSPECT).handler(
        {"target_id": "local", "probe": True}, context
    )

    assert result["data"]["probe"]["status"] == "unsupported"
    assert [event[0][3] for event in telemetry.events] == ["transport.probe"]
    assert telemetry.events[0][1]["extra"]["error_code"] == "unsupported_transport"


def test_cancel_telemetry_matches_job_without_inspection_side_effects() -> None:
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    service = local_ops_service()
    job = service.jobs.submit(
        OperationRequest(
            operation_id="observe-1",
            target_id="local",
            profile_id="host.snapshot",
            session_id="session-1",
        ),
        target_revision=1,
    )
    telemetry = _Telemetry()
    context = ToolExecutionContext(
        channel="cli",
        target="local",
        session_id="session-1",
        telemetryctl=telemetry,
        ops_service=service,
    )
    arguments = {
        "job_id": job.job_id,
        "target_id": "local",
        "session_id": "session-1",
    }

    cancelled = registry.get(TOOL_OPS_JOB_CANCEL).handler(arguments, context)
    inspected = registry.get(TOOL_OPS_JOB_INSPECT).handler(arguments, context)

    assert cancelled["data"]["status"] == "cancelled"
    assert inspected["data"]["status"] == "cancelled"
    assert [event[0][3] for event in telemetry.events] == ["transport.cancel"]
    facts = telemetry.events[0][1]["extra"]
    assert facts["job_id"] == job.job_id
    assert facts["cancel_requested"] is True
    assert facts["remote_outcome"] == "not_dispatched"


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
    service.action_policy = PolicyCtl.with_sqlite(
        ":memory:", config=PolicyConfig(mode="enforce")
    )
    telemetry = _Telemetry()
    context = ToolExecutionContext(
        channel="cli",
        target="local",
        session_id="session-1",
        telemetryctl=telemetry,
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
    assert [event[0][3] for event in telemetry.events] == ["transport.authorization"]
    assert telemetry.events[0][1]["extra"]["remote_outcome"] == "not_dispatched"
    approval_id = denied.data["details"]["approval_id"]
    service.action_policy.resolve_confirmation(approval_id, "allow_once")
    completed = execute_tool_spec_call(
        tool=registry.get(TOOL_OPS_COMMAND_RUN),
        arguments={"plan_id": plan["plan_id"], "plan_hash": plan["plan_hash"]},
        context=context,
    )

    assert denied.ok is False
    assert "confirmation" in denied.error
    assert completed.ok is True
    assert completed.data["status"] == "succeeded"
    assert [event[0][3] for event in telemetry.events] == [
        "transport.authorization",
        "transport.dispatch",
        "transport.result",
    ]
    result_facts = telemetry.events[-1][1]["extra"]
    assert result_facts["job_id"] == completed.data["job_id"]
    assert result_facts["plan_id"] == plan["plan_id"]
    assert result_facts["attempt_phase"] == "terminal"
    assert result_facts["remote_outcome"] == "exit_observed"
    assert result_facts["approval_id"] == approval_id
    assert result_facts["policy_grant_id"]


def test_focus_tool_adapter_resolves_ops_confirmation_once(tmp_path) -> None:
    registry = ToolRegistry()
    REGISTRAR.register(registry)
    policy = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    service = local_ops_service()
    service.action_policy = policy
    plan = service.plan_command(
        target_id="local", argv=("printf", "ready"), session_id="session-1"
    )
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        runtime_registry=registry,
        policy_ctl=policy,
        ops_service=service,
        artifactctl=SimpleNamespace(),
    )
    approvals = []
    adapter.set_approval_callback(
        lambda tool, args, approval_id: (
            approvals.append((tool, args, approval_id)) or True
        )
    )

    result = adapter.execute(
        command={
            "tool_name": TOOL_OPS_COMMAND_RUN,
            "args": {"plan_id": plan.plan_id, "plan_hash": plan.plan_hash},
        },
        session_id="session-1",
        trace_id="trace-1",
    )

    assert result["status"] == "success"
    assert result["outputs"]["data"]["status"] == "succeeded"
    assert len(approvals) == 1
    assert service.jobs.list()[0].policy_grant_id


def test_focus_tool_adapter_returns_policy_resolution_error(tmp_path) -> None:
    policy = PolicyCtl.with_sqlite(
        tmp_path / "policy.db", config=PolicyConfig(mode="enforce")
    )
    service = local_ops_service()
    service.action_policy = policy
    plan = service.plan_command(
        target_id="local", argv=("printf", "ready"), session_id="session-1"
    )
    adapter = ToolAdapter(
        workspace_root=tmp_path,
        policy_ctl=policy,
        ops_service=service,
        artifactctl=SimpleNamespace(),
    )
    adapter.set_approval_callback(lambda *_args: True)
    policy.resolve_confirmation = MagicMock(
        side_effect=PolicyControlError(
            "PENDING_CONFIRMATION_EXPIRED",
            "Pending confirmation expired.",
        )
    )

    result = adapter.execute(
        command={
            "tool_name": TOOL_OPS_COMMAND_RUN,
            "args": {"plan_id": plan.plan_id, "plan_hash": plan.plan_hash},
        },
        session_id="session-1",
        trace_id="trace-1",
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "PENDING_CONFIRMATION_EXPIRED"
