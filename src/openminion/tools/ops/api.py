from __future__ import annotations

from typing import Any

from .contracts import EvidenceRecord, OperationTarget
from .guidance import OPS_GUIDANCE_ID
from .interfaces import ALL_OPS_TOOLS
from .service import OpsService

_DEPENDENCIES = {
    "ssh": "install the 'remote' extra",
    "winrm": "install the 'remote-winrm' extra",
    "kubernetes": "install the 'remote-kubernetes' extra",
    "ssm": "install the 'remote-aws' extra",
}


def target_view(target: OperationTarget) -> dict[str, Any]:
    """Return the public target contract without credential or trust details."""
    return {
        "target_id": target.target_id,
        "display_label": target.display_label,
        "kind": target.kind,
        "platform": target.platform,
        "environment": target.environment,
        "ssh_auth_mode": target.ssh_auth_mode if target.kind == "ssh" else "",
        "policy_profile": target.policy_profile,
        "capabilities": target.capabilities,
        "workspace_scopes": target.workspace_scopes,
        "log_scopes": target.log_scopes,
        "service_scopes": target.service_scopes,
        "max_concurrency": target.max_concurrency,
        "timeout_seconds": target.timeout_seconds,
        "maintenance_window": target.maintenance_window,
        "enabled": target.enabled,
        "labels": target.labels,
        "revision": target.revision,
        "credential_configured": target.credential_ref is not None,
        "endpoint_trust_configured": bool(
            target.endpoint_trust.host_key or target.endpoint_trust.known_hosts_path
        ),
    }


def operator_state(service: OpsService) -> dict[str, Any]:
    """Return the shared operator view used by CLI, API, and TUI adapters."""
    targets = service.list_targets()
    disabled = {}
    for target in targets:
        readiness = service.inspect_target_readiness(target.target_id)
        if not readiness["dependency_available"]:
            disabled[target.target_id] = _DEPENDENCIES[target.kind]
        elif not readiness["transport_available"]:
            disabled[target.target_id] = "transport is unavailable"
    target_payloads = []
    for target in targets:
        payload = target_view(target)
        payload.update(service.inspect_target_readiness(target.target_id))
        payload["transport_capabilities"] = service.transport_capabilities(
            target.target_id
        )
        target_payloads.append(payload)
    awaiting_jobs = []
    for job in service.jobs.list():
        if job.attempt_phase != "awaiting_approval":
            continue
        payload = job_view(job)
        payload["approval_validity"] = "unverified"
        awaiting_jobs.append(payload)
    return {
        "ok": True,
        "data": {
            "tool_family": {
                "id": "ops",
                "tools": list(ALL_OPS_TOOLS),
                "guidance": OPS_GUIDANCE_ID,
            },
            "targets": target_payloads,
            "jobs": [job_view(job) for job in service.jobs.list()],
            "plans": [plan.model_dump(mode="json") for plan in service.plans.list()],
            "evidence": [
                item.model_dump(mode="json") for item in service.list_evidence()
            ],
            "approval_awaiting_jobs": awaiting_jobs,
            "disabled_reasons": disabled,
        },
    }


def target_list(service: OpsService) -> dict[str, Any]:
    return {
        "ok": True,
        "data": [target_view(item) for item in service.list_targets()],
    }


def target_inspect(
    service: OpsService, target_id: str, *, probe: bool = False
) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            **target_view(service.inspect_target(target_id)),
            **service.inspect_target_readiness(target_id, probe=probe),
        },
    }


def job_inspect(service: OpsService, job_id: str) -> dict[str, Any]:
    job = service.inspect_job(job_id)
    evidence = service.inspect_evidence(job.evidence_id) if job.evidence_id else None
    return {
        "ok": True,
        "data": job_result_view(job, evidence),
    }


def job_view(job: Any) -> dict[str, Any]:
    payload: dict[str, Any] = job.model_dump(mode="json")
    current_liveness = (
        "unverified"
        if job.status == "running" or job.attempt_phase == "claimed"
        else "not_running"
    )
    payload["current_liveness"] = current_liveness
    payload["reconciliation_required"] = current_liveness == "unverified"
    if current_liveness == "unverified":
        payload["reconciliation_hint"] = (
            "Inspect remote state, then use the existing mark-interrupted action "
            "only if this attempt is no longer running."
        )
    return payload


def evidence_view(evidence: EvidenceRecord) -> dict[str, Any]:
    """Return bounded evidence facts suitable for a model-facing tool result."""
    return {
        "evidence_id": evidence.evidence_id,
        "operation_id": evidence.operation_id,
        "target_id": evidence.target_id,
        "target_revision": evidence.target_revision,
        "transport": evidence.transport,
        "profile_id": evidence.profile_id,
        "tool_id": evidence.tool_id,
        "claim_status": evidence.claim_status,
        "collected_at": evidence.collected_at,
        "output_digest": evidence.output_digest,
        "stdout_preview": evidence.stdout_preview,
        "stderr_preview": evidence.stderr_preview,
        "return_code": evidence.return_code,
        "reason": evidence.reason,
        "policy_outcome": evidence.policy_outcome,
        "approval_id": evidence.approval_id,
        "command_hash": evidence.command_hash,
        "timed_out": evidence.timed_out,
        "cancelled": evidence.cancelled,
        "truncated": evidence.truncated,
    }


def job_result_view(job: Any, evidence: EvidenceRecord | None = None) -> dict[str, Any]:
    payload = job_view(job)
    if evidence is not None:
        payload["evidence"] = evidence_view(evidence)
    return payload


def evidence_list(
    service: OpsService,
    *,
    target_id: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    return {
        "ok": True,
        "data": [
            item.model_dump(mode="json")
            for item in service.list_evidence(
                target_id=target_id,
                session_id=session_id,
            )
        ],
    }
