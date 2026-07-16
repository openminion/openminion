from __future__ import annotations

from .contracts import ToolExposureProfile, ToolRiskAnnotations

_OPS_READ_TOOLS = frozenset(
    {
        "ops.command.observe",
        "ops.host.snapshot",
        "ops.job.inspect",
        "ops.logs.query",
        "ops.network.inspect",
        "ops.service.inspect",
        "ops.target.inspect",
        "ops.target.list",
    }
)

_SPECIALIZED_TOOL_FAMILY_PREFIXES = (
    "cloud.",
    "config.",
    "gitops.",
    "iac.",
    "k8s.",
    "observability.",
)


def requires_explicit_exposure_profile(tool_name: str) -> bool:
    name = str(tool_name or "").strip()
    return name.startswith(_SPECIALIZED_TOOL_FAMILY_PREFIXES)


def default_exposure_profiles() -> tuple[ToolExposureProfile, ...]:
    return (
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
            tool_names=frozenset({"ops.job.cancel"}),
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
        ToolExposureProfile(
            profile_id="k8s_readonly",
            title="Kubernetes inspection",
            summary="Read-only cluster, workload, event, and log inspection.",
            tool_names=frozenset(
                {
                    "k8s.cluster.inspect",
                    "k8s.events.query",
                    "k8s.logs.query",
                    "k8s.workload.inspect",
                }
            ),
            target_kinds=frozenset({"kubernetes"}),
            credential_scopes=frozenset({"k8s.read"}),
            dependencies=frozenset({"kubernetes"}),
            evidence_expectations=(
                "cite cluster, namespace, and observed resource revision",
            ),
            stop_rules=("stop before mutation or when cluster identity is ambiguous",),
            guidance_names=("k8s-readonly-inspection",),
        ),
        ToolExposureProfile(
            profile_id="iac_plan",
            title="Infrastructure planning",
            summary="Inspect infrastructure code and produce non-applying plans.",
            tool_names=frozenset({"iac.inspect", "iac.plan"}),
            risk=ToolRiskAnnotations(tier="plan"),
            dependencies=frozenset({"iac"}),
            evidence_expectations=("save plan hash and bounded plan summary",),
            stop_rules=("stop before apply",),
            guidance_names=("iac-plan-review",),
        ),
        ToolExposureProfile(
            profile_id="config_check",
            title="Configuration checks",
            summary="Inspect configuration-management inventory and run check-mode validation.",
            tool_names=frozenset({"config.inventory", "config.check"}),
            risk=ToolRiskAnnotations(tier="plan"),
            dependencies=frozenset({"config_management"}),
            evidence_expectations=("record inventory scope and check-mode result",),
            stop_rules=("stop before applying configuration",),
            guidance_names=("config-check-review",),
        ),
        ToolExposureProfile(
            profile_id="observability_readonly",
            title="Observability inspection",
            summary="Query metrics, logs, traces, and alerts without mutating monitors.",
            tool_names=frozenset(
                {
                    "observability.alerts.query",
                    "observability.logs.query",
                    "observability.metrics.query",
                    "observability.traces.query",
                }
            ),
            credential_scopes=frozenset({"observability.read"}),
            evidence_expectations=(
                "record source, query window, and bounded result ids",
            ),
            stop_rules=("stop before monitor or alert mutation",),
            guidance_names=("observability-triage",),
        ),
        ToolExposureProfile(
            profile_id="gitops_readonly",
            title="GitOps inspection",
            summary="Inspect desired state, synchronization, and drift without reconciling.",
            tool_names=frozenset(
                {"gitops.application.inspect", "gitops.drift.inspect"}
            ),
            credential_scopes=frozenset({"gitops.read"}),
            evidence_expectations=("record application revision and drift evidence",),
            stop_rules=("stop before reconciliation",),
            guidance_names=("gitops-drift-review",),
        ),
        ToolExposureProfile(
            profile_id="cloud_ops_readonly",
            title="Cloud operations inspection",
            summary="Inspect cloud resources and account posture without applying changes.",
            tool_names=frozenset({"cloud.account.inspect", "cloud.resource.inspect"}),
            credential_scopes=frozenset({"cloud.read"}),
            evidence_expectations=("record account, region, role, and resource ids",),
            stop_rules=("stop before remote command or resource mutation",),
            guidance_names=("cloud-operations-inspection",),
        ),
    )


__all__ = ["default_exposure_profiles", "requires_explicit_exposure_profile"]
