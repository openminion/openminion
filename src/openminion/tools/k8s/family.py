from openminion.modules.tool.exposure import ToolExposureProfile
from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .args import (
    WorkloadGetArgs,
    WorkloadListArgs,
    K8sEventsArgs,
    K8sLogsArgs,
    RolloutStatusArgs,
)
from .interfaces import (
    ALL_K8S_TOOLS,
    TOOL_K8S_WORKLOAD_GET,
    TOOL_K8S_WORKLOAD_LIST,
    TOOL_K8S_EVENTS_LIST,
    TOOL_K8S_LOGS_GET,
    TOOL_K8S_ROLLOUT_STATUS,
)
from .plugin import (
    _h_workload_get,
    _h_workload_list,
    _h_events_list,
    _h_logs_get,
    _h_rollout_status,
)

K8S_FAMILY = ToolFamilySpec(
    module_id="k8s",
    min_scope_default="READ_ONLY",
    common_tags=("plugin", "ops", "k8s"),
    exposure_profiles=(
        ToolExposureProfile(
            profile_id="k8s_readonly",
            title="Kubernetes",
            summary="Read-only Kubernetes workload, event, log, and rollout inspection.",
            tool_names=frozenset(ALL_K8S_TOOLS),
            target_kinds=frozenset({"ops-target"}),
            dependencies=frozenset(),
            evidence_expectations=("return fixture/live source and evidence digest",),
            stop_rules=("stop before mutation or unscoped provider access",),
            guidance_names=("ops.safety.v1",),
            activation_hint="Activate explicitly for a scoped read-only operations task.",
        ),
    ),
    tools=(
        ToolDecl(
            TOOL_K8S_WORKLOAD_GET,
            WorkloadGetArgs,
            _h_workload_get,
            "Get one workload in an allowlisted context and namespace.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_K8S_WORKLOAD_LIST,
            WorkloadListArgs,
            _h_workload_list,
            "List workloads in an allowlisted context and namespace.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_K8S_EVENTS_LIST,
            K8sEventsArgs,
            _h_events_list,
            "List bounded Kubernetes events.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_K8S_LOGS_GET,
            K8sLogsArgs,
            _h_logs_get,
            "Read a bounded pod log window.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_K8S_ROLLOUT_STATUS,
            RolloutStatusArgs,
            _h_rollout_status,
            "Inspect rollout status without mutating it.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
    ),
)

__all__ = ["K8S_FAMILY"]
