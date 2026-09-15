from __future__ import annotations

from typing import Any

from .contracts import OperationTarget
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
    target_payloads = []
    for target in targets:
        payload = target_view(target)
        payload.update(service.inspect_target_readiness(target.target_id))
        payload["transport_capabilities"] = service.transport_capabilities(
            target.target_id
        )
        payload["transport_ready"] = target.target_id not in disabled
        target_payloads.append(payload)
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
            "pending_approvals": [],
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
    return {
        "ok": True,
        "data": job_view(service.inspect_job(job_id)),
    }


def job_view(job: Any) -> dict[str, Any]:
    payload: dict[str, Any] = job.model_dump(mode="json")
    payload["current_liveness"] = (
        "unverified"
        if job.status == "running" or job.attempt_phase == "claimed"
        else "not_running"
    )
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
