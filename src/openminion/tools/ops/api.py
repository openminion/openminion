from __future__ import annotations

import importlib.util
from typing import Any

from .contracts import OperationTarget
from .guidance import OPS_GUIDANCE_ID
from .interfaces import ALL_OPS_TOOLS
from .service import OpsService


def target_view(target: OperationTarget) -> dict[str, Any]:
    """Return the public target contract without credential or trust details."""
    return {
        "target_id": target.target_id,
        "display_label": target.display_label,
        "kind": target.kind,
        "platform": target.platform,
        "environment": target.environment,
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
    disabled = {
        target.target_id: "install the 'remote' extra to enable SSH operations"
        for target in targets
        if target.kind == "ssh" and importlib.util.find_spec("asyncssh") is None
    }
    return {
        "ok": True,
        "data": {
            "tool_family": {
                "id": "ops",
                "tools": list(ALL_OPS_TOOLS),
                "guidance": OPS_GUIDANCE_ID,
            },
            "targets": [target_view(target) for target in targets],
            "jobs": [job.model_dump(mode="json") for job in service.jobs.list()],
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


def target_inspect(service: OpsService, target_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "data": target_view(service.inspect_target(target_id)),
    }


def job_inspect(service: OpsService, job_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "data": service.inspect_job(job_id).model_dump(mode="json"),
    }


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
