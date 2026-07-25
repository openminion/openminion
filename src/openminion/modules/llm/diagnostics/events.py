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
    normalized = str(operation or "").strip().lower()
    if normalized not in _ALLOWED_OPERATIONS:
        return False

    payload_extra: dict[str, Any] = {}
    provider_name = str(provider or "").strip()
    if provider_name:
        payload_extra["provider"] = provider_name
    model_name = str(model or "").strip()
    if model_name:
        payload_extra["model"] = model_name
    if attempt is not None:
        try:
            payload_extra["attempt"] = int(attempt)
        except (TypeError, ValueError):
            pass
    error_name = str(error_code or "").strip().upper()
    if error_name:
        payload_extra["error_code"] = error_name
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

    normalized_session_id = str(session_id or "").strip()
    normalized_turn_id = str(turn_id or "").strip()
    if not normalized_session_id or not normalized_turn_id:
        return False

    normalized_outcome = str(outcome or "").strip().lower()
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
        normalized_value = str(value or "").strip()
        if normalized_value:
            payload[key] = normalized_value
    if attempt is not None:
        try:
            payload["attempt"] = int(attempt)
        except (TypeError, ValueError):
            pass

    return emit_module_telemetry(
        telemetryctl,
        "emit_canonical_event",
        normalized_session_id,
        normalized_turn_id,
        event_type,
        payload,
    )
