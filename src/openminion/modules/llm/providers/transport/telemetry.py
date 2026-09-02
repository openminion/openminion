from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from openminion.modules.telemetry.events.module import (
    emit_module_counter,
    emit_module_operation,
    emit_module_telemetry,
)

_LOG = logging.getLogger(__name__)


def elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _correlation(
    trace_metadata: Mapping[str, Any] | None,
) -> tuple[str, str, dict[str, str]] | None:
    metadata = trace_metadata or {}
    session_id = str(metadata.get("session_id") or "").strip()
    turn_id = str(metadata.get("turn_id") or "").strip()
    if not session_id or not turn_id:
        return None
    extra = {
        key: str(metadata.get(key) or "").strip()
        for key in ("invocation_id", "execution_id", "trace_id")
        if str(metadata.get(key) or "").strip()
    }
    return session_id, turn_id, extra


def _emit(telemetryctl: Any, method_name: str, *args: Any, **kwargs: Any) -> bool:
    return bool(
        emit_module_telemetry(
            telemetryctl,
            method_name,
            *args,
            logger=_LOG,
            **kwargs,
        )
    )


def emit_transport_timeout_counter(
    telemetryctl: Any | None,
    *,
    provider_name: str,
    method: str,
    reason: str,
    trace_metadata: Mapping[str, Any] | None,
) -> None:
    correlation = _correlation(trace_metadata)
    if telemetryctl is None or correlation is None:
        return
    session_id, turn_id, correlation_extra = correlation
    emit_module_counter(
        emit_module_telemetry_fn=lambda *args, **kwargs: _emit(
            telemetryctl, *args, **kwargs
        ),
        session_id=session_id,
        turn_id=turn_id,
        module_id="openminion-llm",
        counter_name="llm_transport_timeout",
        value=1.0,
        status="error",
        extra={
            "provider": provider_name.strip(),
            "method": method.strip().upper(),
            "reason": reason.strip(),
            **correlation_extra,
        },
    )


def emit_transport_performance(
    telemetryctl: Any | None,
    *,
    provider_name: str,
    method: str,
    status: str,
    request_build_ms: int | None = None,
    round_trip_ms: int | None = None,
    response_open_ms: int | None = None,
    first_event_ms: int | None = None,
    parse_ms: int | None = None,
    total_ms: int | None = None,
    request_bytes: int | None = None,
    response_bytes: int | None = None,
    retry_count: int = 0,
    reason: str = "",
    transport: str = "urllib",
    trace_metadata: Mapping[str, Any] | None = None,
) -> None:
    correlation = _correlation(trace_metadata)
    if telemetryctl is None or correlation is None:
        return
    session_id, turn_id, correlation_extra = correlation
    extra = {
        "provider": provider_name.strip(),
        "method": method.strip().upper(),
        "transport": transport,
        "request_build_ms": request_build_ms,
        "provider_round_trip_ms": round_trip_ms,
        "response_open_ms": response_open_ms,
        "first_event_ms": first_event_ms,
        "parse_ms": parse_ms,
        "total_ms": total_ms,
        "request_bytes": request_bytes,
        "response_bytes": response_bytes,
        "retry_count": retry_count,
        **correlation_extra,
    }
    if reason:
        extra["reason"] = reason.strip()
    emit_module_operation(
        emit_module_telemetry_fn=lambda *args, **kwargs: _emit(
            telemetryctl, *args, **kwargs
        ),
        session_id=session_id,
        turn_id=turn_id,
        module_id="openminion-llm",
        operation=f"http_json_{method.strip().lower()}",
        status=status,
        extra=extra,
    )


__all__ = [
    "elapsed_ms",
    "emit_transport_performance",
    "emit_transport_timeout_counter",
]
