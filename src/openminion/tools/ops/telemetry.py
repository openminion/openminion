from __future__ import annotations

import hashlib
import logging
from typing import Any

from openminion.modules.telemetry.events.module import emit_module_telemetry

from .contracts import EvidenceRecord, OperationTarget

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
        },
        logger=_LOGGER,
    )
