import logging
from typing import Any

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
