from __future__ import annotations

from openminion.modules.tool.exposure import ToolExposureProfile, ToolRiskAnnotations
from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .args import (
    EmptyArgs,
    JobArgs,
    LogsArgs,
    ObservationArgs,
    ProfileArgs,
    ServiceArgs,
    TargetArgs,
)
from .interfaces import (
    TOOL_OPS_COMMAND_OBSERVE,
    TOOL_OPS_HOST_SNAPSHOT,
    TOOL_OPS_JOB_CANCEL,
    TOOL_OPS_JOB_INSPECT,
    TOOL_OPS_LOGS_QUERY,
    TOOL_OPS_NETWORK_INSPECT,
    TOOL_OPS_SERVICE_INSPECT,
    TOOL_OPS_TARGET_INSPECT,
    TOOL_OPS_TARGET_LIST,
)
from .plugin import (
    _command_observe,
    _job_cancel,
    _job_inspect,
    _logs_query,
    _profile,
    _service_inspect,
    _target_inspect,
    _target_list,
)

_OPS_READ_TOOLS = frozenset(
    {
        TOOL_OPS_COMMAND_OBSERVE,
        TOOL_OPS_HOST_SNAPSHOT,
        TOOL_OPS_JOB_INSPECT,
        TOOL_OPS_LOGS_QUERY,
        TOOL_OPS_NETWORK_INSPECT,
        TOOL_OPS_SERVICE_INSPECT,
        TOOL_OPS_TARGET_INSPECT,
        TOOL_OPS_TARGET_LIST,
    }
)


OPS_FAMILY = ToolFamilySpec(
    module_id="ops",
    min_scope_default="READ_ONLY",
    common_tags=("plugin", "ops"),
    exposure_profiles=(
        ToolExposureProfile(
            profile_id="ops_minimal",
            title="System operations",
            summary="Read-only host, service, log, network, target, and job inspection.",
            tool_names=_OPS_READ_TOOLS,
            evidence_expectations=("cite target and evidence ids",),
            stop_rules=(
                "stop when evidence is incomplete or target identity is unclear",
            ),
            guidance_names=(
                "ops-linux-diagnostics",
                "ops-incident-handoff",
                "ops.safety.v1",
            ),
            default_active=True,
        ),
        ToolExposureProfile(
            profile_id="ops_job_control",
            title="Operations job control",
            summary="Cancel a durable operations job after explicit activation and approval.",
            tool_names=frozenset({TOOL_OPS_JOB_CANCEL}),
            risk=ToolRiskAnnotations(
                tier="apply",
                requires_approval=True,
                mutates_state=True,
            ),
            evidence_expectations=("record job and approval ids",),
            stop_rules=("stop when approval or job identity is missing",),
            guidance_names=("ops.safety.v1",),
            activation_hint="Activate only for an approved operations job-control task.",
        ),
    ),
    tools=(
        ToolDecl(
            TOOL_OPS_TARGET_LIST,
            EmptyArgs,
            _target_list,
            "List configured operations targets available for inspection.",
            idempotent=True,
            tags=("observation",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_OPS_TARGET_INSPECT,
            TargetArgs,
            _target_inspect,
            "Inspect one configured operations target and its transport metadata.",
            idempotent=True,
            tags=("observation",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_OPS_HOST_SNAPSHOT,
            ObservationArgs,
            _profile("host.snapshot", TOOL_OPS_HOST_SNAPSHOT),
            "Collect a bounded read-only host snapshot with evidence.",
            idempotent=True,
            tags=("observation",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_OPS_SERVICE_INSPECT,
            ServiceArgs,
            _service_inspect,
            "Inspect a service on an operations target without changing it.",
            idempotent=True,
            tags=("observation",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_OPS_LOGS_QUERY,
            LogsArgs,
            _logs_query,
            "Query a bounded service log window and return evidence.",
            idempotent=True,
            tags=("observation",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_OPS_NETWORK_INSPECT,
            ObservationArgs,
            _profile("network.inspect", TOOL_OPS_NETWORK_INSPECT),
            "Inspect network state on an operations target without changing it.",
            idempotent=True,
            tags=("observation",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_OPS_COMMAND_OBSERVE,
            ProfileArgs,
            _command_observe,
            "Run an allowlisted read-only observation profile and capture evidence.",
            idempotent=True,
            tags=("observation",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_OPS_JOB_INSPECT,
            JobArgs,
            _job_inspect,
            "Inspect a durable operations job and its evidence state.",
            idempotent=True,
            tags=("observation",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_OPS_JOB_CANCEL,
            JobArgs,
            _job_cancel,
            "Cancel a durable operations job after exposure and approval checks.",
            idempotent=True,
            tags=("operation_control",),
            capabilities=("operation_control", "ops", "evidence"),
        ),
    ),
)


__all__ = ["OPS_FAMILY"]
