import logging

from openminion.modules.telemetry.events.module import make_module_emitters

_LOGGER = logging.getLogger(__name__)
_MODULE_ID = "openminion-brain"
_ALLOWED_OPERATIONS = frozenset(
    {
        "turn_start",
        "llm_pack",
        "tool_loop",
        "retry",
        "request_readiness",
        "plan_review",
        "request_handoff",
        "turn_finish",
    }
)

_emitters = make_module_emitters(
    module_id=_MODULE_ID,
    allowed_operations=_ALLOWED_OPERATIONS,
    logger=_LOGGER,
)
emit_module_telemetry = _emitters.emit_module_telemetry
emit_brain_operation = _emitters.emit_operation


def emit_request_readiness_operation(
    *,
    telemetryctl: object | None = None,
    session_id: str,
    turn_id: str,
    readiness: object | None,
    status: str = "ok",
) -> None:
    if readiness is None:
        emit_brain_operation(
            telemetryctl=telemetryctl,
            session_id=session_id,
            turn_id=turn_id,
            operation="request_readiness",
            status=status,
            extra={"present": False},
        )
        return
    emit_brain_operation(
        telemetryctl=telemetryctl,
        session_id=session_id,
        turn_id=turn_id,
        operation="request_readiness",
        status=status,
        extra={
            "present": True,
            "posture": str(getattr(readiness, "posture", "") or "").strip(),
            "requested_outcome": str(
                getattr(readiness, "requested_outcome", "") or ""
            ).strip(),
            "state": str(getattr(readiness, "state", "") or "").strip(),
            "assumption_count": len(list(getattr(readiness, "assumptions", []) or [])),
        },
    )
