from __future__ import annotations

import logging
from typing import Any, Mapping

from openminion.modules.telemetry.events.module import make_module_emitters

_LOGGER = logging.getLogger(__name__)
_MODULE_ID = "openminion-retrieve"
_ALLOWED_OPERATIONS = frozenset({"query", "rerank", "fallback"})

_emitters = make_module_emitters(
    module_id=_MODULE_ID,
    allowed_operations=_ALLOWED_OPERATIONS,
    logger=_LOGGER,
)
emit_module_telemetry = _emitters.emit_module_telemetry
emit_retrieve_operation = _emitters.emit_operation
emit_retrieve_counter = _emitters.emit_counter


def format_score_breakdown(candidate: Mapping[str, Any]) -> str:
    meta = candidate.get("meta", {})
    if not isinstance(meta, Mapping):
        return ""
    breakdown = meta.get("score_breakdown", {})
    if not isinstance(breakdown, Mapping):
        return ""
    parts: list[str] = []
    for key in (
        "relevance",
        "recency",
        "feedback",
        "type_bonus",
        "confidence",
        "outcome_utility",
        "unified_score",
    ):
        if key not in breakdown:
            continue
        try:
            parts.append(f"{key}={float(breakdown[key]):.3f}")
        except (TypeError, ValueError):
            continue
    return ";".join(parts)


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
    if not session_id or not turn_id:
        return
    bucket_ms = float(int(max(0.0, latency_ms) // 10) * 10)
    payload_extra = dict(extra or {})
    payload_extra["latency_bucket_ms"] = bucket_ms
    emit_retrieve_operation(
        telemetryctl=telemetryctl,
        session_id=session_id,
        turn_id=turn_id,
        operation=operation,
        status=status,
        extra=payload_extra,
    )
    for counter_name, value in (
        ("returned_items", float(max(0, result_count))),
        ("latency_bucket_ms", bucket_ms),
        ("token_estimate", float(max(0, token_estimate))),
    ):
        emit_retrieve_counter(
            telemetryctl=telemetryctl,
            session_id=session_id,
            turn_id=turn_id,
            counter_name=counter_name,
            value=value,
            status=status,
            extra=payload_extra,
        )
