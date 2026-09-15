from __future__ import annotations

import hashlib
import logging
from typing import Any

from openminion.modules.telemetry.events.module import emit_module_telemetry

from .contracts import EvidenceRecord, OperationJob, OperationTarget

_LOGGER = logging.getLogger(__name__)


def emit_transport_event(
    ctx: Any,
    *,
    phase: str,
    target: OperationTarget,
    capability: str,
    status: str,
    duration_ms: int = 0,
    evidence: EvidenceRecord | None = None,
    job: OperationJob | None = None,
    error_code: str = "",
) -> bool:
    provider_digest = ""
    if evidence is not None and evidence.provider_request_id:
        provider_digest = hashlib.sha256(
            evidence.provider_request_id.encode()
        ).hexdigest()
    metadata = getattr(ctx, "metadata", {})
    turn_id = (
        str(metadata.get("turn_id") or metadata.get("trace_id") or "")
        if isinstance(metadata, dict)
        else ""
    )
    return emit_module_telemetry(
        getattr(ctx, "telemetryctl", None),
        "emit_module_operation",
        str(getattr(ctx, "session_id", "") or ""),
        turn_id,
        "ops",
        f"transport.{phase}",
        count=1,
        status=status,
        extra={
            "target_id": target.target_id,
            "target_revision": target.revision,
            "transport_kind": target.kind,
            "capability": capability,
            "duration_ms": max(0, duration_ms),
            "timed_out": bool(evidence and evidence.timed_out),
            "truncated": bool(evidence and evidence.truncated),
            "error_code": error_code,
            "provider_request_id_digest": provider_digest,
            "job_id": job.job_id if job else "",
            "plan_id": job.plan_id if job else "",
            "attempt_phase": job.attempt_phase if job else "",
            "cancel_requested": bool(job and job.cancel_requested),
            "cancel_status": job.cancel_status if job else "",
            "remote_outcome": job.remote_outcome if job else "",
            "approval_id": job.approval_id if job else "",
            "policy_grant_id": job.policy_grant_id if job else "",
            "policy_invocation_hash": job.policy_invocation_hash if job else "",
        },
        logger=_LOGGER,
    )
