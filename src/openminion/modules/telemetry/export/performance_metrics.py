"""Low-cardinality performance metric derivation for OTel export."""

from __future__ import annotations

from typing import Any

from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.trace.phase_timing import CHAT_PHASES

_KIND_COUNTER = "counter"
_KIND_GAUGE = "gauge"
_KIND_HISTOGRAM = "histogram"

_ALLOWED_LABELS = frozenset(
    {
        "phase",
        "scenario_id",
        "route_class",
        "transport",
        "profile_kind",
        "module_family",
        "cold_start",
        "outcome",
        "store_family",
        "operation",
        "criticality",
        "segment_family",
        "tool_family",
        "cache_family",
        "error_family",
        "view_family",
        "process_family",
    }
)
_FORBIDDEN_LABELS = frozenset(
    {
        "session_id",
        "turn_id",
        "prompt",
        "response",
        "raw_prompt",
        "raw_response",
        "file_path",
        "path",
        "provider_key",
        "api_key",
        "model",
        "exception",
        "tool_input",
        "plugin_path",
        "skill_path",
    }
)


def performance_metrics_for_event(event: TelemetryEvent) -> list[dict[str, Any]]:
    payload = event.data if isinstance(event.data, dict) else {}
    event_type = str(event.event_type or "").strip()
    if event_type == "chat.phase_timing":
        return _chat_phase_metrics(payload)
    if event_type in {"llm.call.completed", "llm_call"}:
        return _model_provider_metrics(payload)
    if event_type.startswith("tool."):
        return _tool_execution_metrics(payload)
    if event_type in {"storage.query", "storage.slow_query"}:
        return _storage_operation_metrics(payload)
    if event_type == "storage.pool.stats":
        return _storage_pool_metrics(payload)
    if event_type == "telemetry.queue.stats":
        return _telemetry_queue_metrics(payload)
    if event_type == "module.stats":
        return _module_stats_metrics(payload)
    if event_type == "llm.cache.metrics":
        return _cache_metrics(payload)
    if event_type == "tui.render":
        return _tui_render_metrics(payload)
    return []


def _chat_phase_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    route_class = _bounded_label(
        payload.get("route_class")
        or payload.get("process_mode")
        or payload.get("transport")
        or "runtime",
        default="runtime",
    )
    transport = _bounded_label(payload.get("transport") or "runtime", default="runtime")
    cold_start = "true" if bool(payload.get("cold_start")) else "false"
    outcome = _outcome_label(payload)
    common = {"route_class": route_class, "outcome": outcome, "cold_start": cold_start}
    metrics: list[dict[str, Any]] = []
    _append_metric(
        metrics,
        "openminion_turn_wall_ms",
        _KIND_HISTOGRAM,
        payload.get("total_turn_ms"),
        common,
    )
    _append_metric(
        metrics,
        "openminion_turn_ttft_ms",
        _KIND_HISTOGRAM,
        payload.get("time_to_first_text_ms"),
        common,
    )
    for phase in CHAT_PHASES:
        _append_metric(
            metrics,
            "openminion_chat_phase_duration_ms",
            _KIND_HISTOGRAM,
            payload.get(f"{phase}_ms"),
            {**common, "phase": phase},
        )
    _append_metric(
        metrics,
        "openminion_provider_round_trip_ms",
        _KIND_HISTOGRAM,
        payload.get("provider_round_trip_ms"),
        {"route_class": route_class, "transport": transport, "outcome": outcome},
    )
    _append_metric(
        metrics,
        "openminion_context_assembly_ms",
        _KIND_HISTOGRAM,
        payload.get("context_pack_build_ms"),
        {"route_class": route_class, "outcome": outcome},
    )
    return metrics


def _model_provider_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    common = {
        "transport": _bounded_label(payload.get("transport") or "runtime", default="runtime"),
        "profile_kind": _bounded_label(
            payload.get("profile_kind") or payload.get("provider_profile") or "runtime",
            default="runtime",
        ),
        "outcome": _outcome_label(payload),
        "cache_family": _bounded_label(
            payload.get("cache_family") or "llm", default="llm"
        ),
    }
    _append_metric(
        metrics,
        "openminion_model_calls_total",
        _KIND_COUNTER,
        _first_present(payload, "call_count", "calls", "count") or 1,
        {
            "transport": common["transport"],
            "profile_kind": common["profile_kind"],
            "outcome": common["outcome"],
        },
    )
    _append_metric(
        metrics,
        "openminion_model_retries_total",
        _KIND_COUNTER,
        _first_present(payload, "retry_count", "retries"),
        {
            "transport": common["transport"],
            "profile_kind": common["profile_kind"],
            "outcome": common["outcome"],
        },
    )
    for metric_name, payload_key in (
        ("openminion_model_request_bytes", "request_bytes"),
        ("openminion_model_response_bytes", "response_bytes"),
        ("openminion_model_input_tokens", "input_tokens"),
        ("openminion_model_output_tokens", "output_tokens"),
        ("openminion_model_cached_tokens", "cached_tokens"),
    ):
        _append_metric(metrics, metric_name, _KIND_HISTOGRAM, payload.get(payload_key), common)
    _append_metric(
        metrics,
        "openminion_provider_round_trip_ms",
        _KIND_HISTOGRAM,
        _first_present(payload, "round_trip_ms", "latency_ms", "elapsed_ms"),
        {
            "transport": common["transport"],
            "profile_kind": common["profile_kind"],
            "outcome": common["outcome"],
        },
    )
    return metrics


def _tool_execution_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    common = {
        "tool_family": _bounded_label(
            payload.get("tool_family")
            or payload.get("tool_name")
            or payload.get("tool")
            or "tool",
            default="tool",
        ),
        "outcome": _outcome_label(payload),
    }
    _append_metric(
        metrics,
        "openminion_tool_calls_total",
        _KIND_COUNTER,
        _first_present(payload, "call_count", "calls", "count") or 1,
        common,
    )
    _append_metric(
        metrics,
        "openminion_tool_duplicate_calls_total",
        _KIND_COUNTER,
        _first_present(payload, "duplicate_call_count", "duplicate_calls"),
        common,
    )
    _append_metric(
        metrics,
        "openminion_tool_duration_ms",
        _KIND_HISTOGRAM,
        _first_present(payload, "duration_ms", "latency_ms", "elapsed_ms"),
        common,
    )
    return metrics


def _storage_operation_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    value = _first_present(payload, "duration_ms", "latency_ms", "elapsed_ms")
    _append_metric(
        metrics,
        "openminion_storage_operation_ms",
        _KIND_HISTOGRAM,
        value,
        {
            "store_family": _bounded_label(
                payload.get("store_family")
                or payload.get("module_id")
                or payload.get("store")
                or "storage",
                default="storage",
            ),
            "operation": _bounded_label(
                payload.get("operation") or payload.get("query_kind") or "operation",
                default="operation",
            ),
            "criticality": _bounded_label(
                payload.get("criticality") or "unknown", default="unknown"
            ),
            "outcome": _outcome_label(payload),
        },
    )
    return metrics


def _storage_pool_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    common = {
        "store_family": _bounded_label(
            payload.get("store_family") or payload.get("module_id") or "storage",
            default="storage",
        ),
        "criticality": _bounded_label(
            payload.get("criticality") or "unknown", default="unknown"
        ),
    }
    _append_metric(
        metrics,
        "openminion_background_write_queue_depth",
        _KIND_GAUGE,
        _first_present(payload, "queue_depth", "depth"),
        common,
    )
    _append_metric(
        metrics,
        "openminion_sqlite_wal_bytes",
        _KIND_GAUGE,
        payload.get("wal_bytes"),
        {"store_family": common["store_family"]},
    )
    return metrics


def _telemetry_queue_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    common = {
        "criticality": _bounded_label(
            payload.get("criticality") or "noncritical", default="noncritical"
        ),
        "outcome": _outcome_label(payload),
    }
    for metric_name, key, kind in (
        ("openminion_telemetry_queue_depth", "queue_depth", _KIND_GAUGE),
        ("openminion_telemetry_queue_drops_total", "drops", _KIND_COUNTER),
        ("openminion_telemetry_flush_failures_total", "flush_failures", _KIND_COUNTER),
        ("openminion_telemetry_flush_latency_ms", "flush_latency_ms", _KIND_HISTOGRAM),
    ):
        _append_metric(metrics, metric_name, kind, payload.get(key), common)
    return metrics


def _module_stats_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    route_class = _bounded_label(
        payload.get("route_class") or payload.get("module_id") or "runtime",
        default="runtime",
    )
    _append_metric(
        metrics,
        "openminion_active_turns",
        _KIND_GAUGE,
        _first_present(payload, "active_turns", "active"),
        {"route_class": route_class},
    )
    _append_metric(
        metrics,
        "openminion_queued_prompts",
        _KIND_GAUGE,
        _first_present(payload, "queued_prompts", "queue_depth"),
        {"route_class": route_class},
    )
    _append_metric(
        metrics,
        "openminion_process_rss_bytes",
        _KIND_GAUGE,
        _first_present(payload, "process_rss_bytes", "rss_bytes"),
        {
            "process_family": _bounded_label(
                payload.get("process_family") or route_class, default="runtime"
            )
        },
    )
    return metrics


def _cache_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    cache_family = _bounded_label(
        payload.get("cache_family") or payload.get("cache") or "llm", default="llm"
    )
    _append_metric(
        metrics,
        "openminion_cache_hits_total",
        _KIND_COUNTER,
        payload.get("hits"),
        {"cache_family": cache_family},
    )
    _append_metric(
        metrics,
        "openminion_cache_misses_total",
        _KIND_COUNTER,
        payload.get("misses"),
        {"cache_family": cache_family},
    )
    return metrics


def _tui_render_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    common = {
        "view_family": _bounded_label(
            payload.get("view_family") or payload.get("view") or "tui", default="tui"
        ),
        "outcome": _outcome_label(payload),
    }
    _append_metric(
        metrics,
        "openminion_tui_render_chunk_ms",
        _KIND_HISTOGRAM,
        _first_present(payload, "render_chunk_ms", "duration_ms", "elapsed_ms"),
        common,
    )
    _append_metric(
        metrics,
        "openminion_tui_queue_pressure",
        _KIND_GAUGE,
        _first_present(payload, "queue_pressure", "queue_depth"),
        common,
    )
    _append_metric(
        metrics,
        "openminion_tui_retained_messages",
        _KIND_GAUGE,
        payload.get("retained_messages"),
        common,
    )
    return metrics


def _append_metric(
    metrics: list[dict[str, Any]],
    name: str,
    kind: str,
    value: Any,
    attributes: dict[str, str],
) -> None:
    number = _optional_float(value)
    if number is None:
        return
    metrics.append(
        {
            "name": name,
            "kind": kind,
            "value": number,
            "attributes": _metric_attributes(attributes),
        }
    )


def _metric_attributes(attributes: dict[str, str]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in attributes.items():
        normalized_key = str(key or "").strip()
        if normalized_key not in _ALLOWED_LABELS or normalized_key in _FORBIDDEN_LABELS:
            continue
        clean[normalized_key] = _bounded_label(value, default="unknown")
    return clean


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number < 0 else number


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    return next((payload[key] for key in keys if key in payload), None)


def _bounded_label(value: Any, *, default: str) -> str:
    text = str(value or "").strip().lower().replace(" ", "_")
    if not text:
        return default
    allowed = [
        char if char.isalnum() or char in {"_", "-", "."} else "_" for char in text[:64]
    ]
    return "".join(allowed) or default


def _outcome_label(payload: dict[str, Any]) -> str:
    raw = str(payload.get("outcome") or payload.get("status") or "ok").strip().lower()
    if raw in {"ok", "success", "completed", "pass"}:
        return "ok"
    if raw in {"error", "failed", "fail", "timeout"}:
        return "error"
    return _bounded_label(raw, default="unknown")
