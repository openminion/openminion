import logging
from typing import Any

from openminion.modules.telemetry.events.catalog import (
    TOOL_ENVELOPE_REPAIR_EXHAUSTED,
    TOOL_ENVELOPE_REPAIR_RETRY,
)
from openminion.modules.telemetry.events.module import (
    emit_module_operation as _emit_module_operation_impl,
    emit_module_telemetry as _emit_module_telemetry_impl,
)

_LOGGER = logging.getLogger(__name__)
_MODULE_ID = "openminion-llm"
_ALLOWED_OPERATIONS = frozenset(
    {
        "request",
        "response",
        "retry",
        "error",
        "cache_hit",
    }
)


def emit_module_telemetry(
    telemetryctl: Any,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> bool:
    return _emit_module_telemetry_impl(
        telemetryctl,
        method_name,
        *args,
        logger=_LOGGER,
        **kwargs,
    )


def emit_llm_operation(
    *,
    telemetryctl: Any,
    session_id: str,
    turn_id: str,
    operation: str,
    provider: str,
    model: str,
    count: int = 1,
    status: str = "ok",
    attempt: int | None = None,
    error_code: str | None = None,
    extra: dict[str, Any] | None = None,
) -> bool:
    normalized = operation.strip().lower()
    if normalized not in _ALLOWED_OPERATIONS:
        return False

    payload_extra = {
        key: value
        for key, value in {
            "provider": provider.strip(),
            "model": model.strip(),
            "error_code": (error_code or "").strip().upper(),
        }.items()
        if value
    }
    if attempt is not None:
        payload_extra["attempt"] = attempt
    if extra:
        payload_extra.update(extra)

    return _emit_module_operation_impl(
        emit_module_telemetry_fn=lambda *args, **kwargs: emit_module_telemetry(
            telemetryctl,
            *args,
            **kwargs,
        ),
        session_id=session_id,
        turn_id=turn_id,
        module_id=_MODULE_ID,
        operation=normalized,
        count=count,
        status=status,
        extra=payload_extra or None,
    )


def emit_tool_envelope_recovery_event(
    *,
    telemetryctl: Any,
    session_id: str,
    turn_id: str,
    outcome: str,
    provider: str = "",
    model: str = "",
    parse_strategy: str = "",
    parse_mode: str = "",
    attempt: int | None = None,
    source: str = "",
    error_code: str = "",
) -> bool:
    """Emit safe tool-envelope recovery telemetry without raw model content."""

    normalized_session_id = session_id.strip()
    normalized_turn_id = turn_id.strip()
    if not normalized_session_id or not normalized_turn_id:
        return False

    normalized_outcome = outcome.strip().lower()
    if normalized_outcome == "retry":
        event_type = TOOL_ENVELOPE_REPAIR_RETRY
        status = "retry"
    elif normalized_outcome == "exhausted":
        event_type = TOOL_ENVELOPE_REPAIR_EXHAUSTED
        status = "error"
    else:
        return False

    payload: dict[str, Any] = {
        "module_id": _MODULE_ID,
        "status": status,
        "recovery_outcome": normalized_outcome,
    }
    for key, value in {
        "provider": provider,
        "model": model,
        "parse_strategy": parse_strategy,
        "parse_mode": parse_mode,
        "source": source,
        "error_code": error_code,
    }.items():
        normalized_value = value.strip()
        if normalized_value:
            payload[key] = normalized_value
    if attempt is not None:
        payload["attempt"] = attempt

    return emit_module_telemetry(
        telemetryctl,
        "emit_canonical_event",
        normalized_session_id,
        normalized_turn_id,
        event_type,
        payload,
    )
