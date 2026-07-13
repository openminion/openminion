from __future__ import annotations

from openminion.modules.capability_pack.schemas import (
    CapabilityPackManifest,
    PackPolicyProfile,
    PackPolicyRule,
    PackSkillMetadata,
    PackToolMetadata,
)

READ_ONLY_TOOLS = (
    "ops.target.list",
    "ops.target.inspect",
    "ops.host.snapshot",
    "ops.service.inspect",
    "ops.logs.query",
    "ops.network.inspect",
    "ops.command.observe",
    "ops.job.inspect",
    "ops.job.cancel",
)


def read_only_manifest() -> CapabilityPackManifest:
    tools = tuple(_tool_metadata(tool_id) for tool_id in READ_ONLY_TOOLS)
    return CapabilityPackManifest(
        pack_id="ops-linux-readonly",
        domain="system_operations",
        version="0.0.1",
        skills=(
            PackSkillMetadata(
                skill_id="ops-linux-diagnostics",
                teaches=("evidence-first host diagnosis",),
                requires_tools=READ_ONLY_TOOLS[:7],
                safe_for_domains=("system_operations",),
                forbidden_claims=("unobserved remediation success",),
                evidence_expectations=("typed operation evidence",),
            ),
            PackSkillMetadata(
                skill_id="ops-incident-handoff",
                teaches=("bounded incident handoff",),
                requires_tools=READ_ONLY_TOOLS,
                safe_for_domains=("system_operations",),
                forbidden_claims=("unsupported root cause",),
                evidence_expectations=("target and evidence ids",),
            ),
        ),
        tools=tools,
        policy_profile=PackPolicyProfile(
            profile_id="ops-linux-readonly",
            rules=(
                PackPolicyRule(
                    verb="read", capability_scope="remote.read", decision="allow"
                ),
            ),
        ),
        eval_suite="ops-linux-readonly-v1",
        audit_profile="system-operations-v1",
        registry_ref="runtime.system_operations.targets",
        risk_model="system-operations-v1",
    )


def _tool_metadata(tool_id: str) -> PackToolMetadata:
    control_tool = tool_id == "ops.job.cancel"
    return PackToolMetadata(
        tool_id=tool_id,
        capabilities=("operation.control" if control_tool else "remote.read",),
        risk_level="med" if control_tool else "low",
        side_effects="local" if control_tool else "none",
        approval_required_for=(),
        result_contract=(
            "system-operations.job.v1"
            if tool_id.startswith("ops.job.")
            else "system-operations.evidence.v1"
        ),
        timeout_policy="bounded",
        audit_events=(
            (
                "system_operations.job_cancelled"
                if control_tool
                else "system_operations.observed"
            ),
        ),
    )
