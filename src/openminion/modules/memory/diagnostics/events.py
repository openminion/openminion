import logging
from typing import Any, Mapping

from openminion.modules.telemetry.events.module import make_module_emitters

_LOGGER = logging.getLogger(__name__)
_MODULE_ID = "openminion-memory"
_ALLOWED_OPERATIONS = frozenset({"query", "rerank", "fallback"})

_emitters = make_module_emitters(
    module_id=_MODULE_ID,
    allowed_operations=_ALLOWED_OPERATIONS,
    logger=_LOGGER,
)
emit_module_telemetry = _emitters.emit_module_telemetry
emit_memory_operation = _emitters.emit_operation
emit_memory_counter = _emitters.emit_counter


def emit_query_metrics(
    *,
    telemetryctl: Any,
    session_id: str,
    turn_id: str,
    operation: str,
    result_count: int,
    latency_ms: float,
    token_estimate: int,
    status: str = "ok",
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Emit memory query metrics (operation + 3 counters)."""
    if not session_id or not turn_id:
        return
    bucket_ms = float(int(max(0.0, latency_ms) // 10) * 10)
    payload_extra = dict(extra or {})
    payload_extra["latency_bucket_ms"] = bucket_ms
    emit_memory_operation(
        telemetryctl=telemetryctl,
        session_id=session_id,
        turn_id=turn_id,
        operation=operation,
        status=status,
        extra=payload_extra,
    )
    emit_memory_counter(
        telemetryctl=telemetryctl,
        session_id=session_id,
        turn_id=turn_id,
        counter_name="returned_items",
        value=float(max(0, result_count)),
        status=status,
        extra=payload_extra,
    )
    emit_memory_counter(
        telemetryctl=telemetryctl,
        session_id=session_id,
        turn_id=turn_id,
        counter_name="latency_bucket_ms",
        value=bucket_ms,
        status=status,
        extra=payload_extra,
    )
    emit_memory_counter(
        telemetryctl=telemetryctl,
        session_id=session_id,
        turn_id=turn_id,
        counter_name="token_estimate",
        value=float(max(0, token_estimate)),
        status=status,
        extra=payload_extra,
    )
