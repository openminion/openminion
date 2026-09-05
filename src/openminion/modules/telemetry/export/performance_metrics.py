"""Low-cardinality performance metric derivation for OTel export."""

from __future__ import annotations

from typing import Any

from openminion.modules.telemetry.schemas import TelemetryEvent
from openminion.modules.telemetry.trace.phase_timing import CHAT_PHASES

_KIND_COUNTER = "counter"
_KIND_GAUGE = "gauge"
_KIND_HISTOGRAM = "histogram"

_GENERIC_METRIC_KINDS = {
    "memory.scope_capacity.evicted": _KIND_COUNTER,
    "memory.soft_deleted.purged": _KIND_COUNTER,
    "tui.render": _KIND_HISTOGRAM,
}
_GENERIC_METRIC_VALUE_KEYS = (
    "value",
    "count",
    "delta",
    "total",
    "size",
    "depth",
    "active",
    "pool_size",
)

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
        "decision",
        "violation_category",
        "business_domain",
        "cost_source",
        "gen_ai.provider.name",
        "gen_ai.response.model",
        "gen_ai.token.type",
    }
)
_FORBIDDEN_LABELS = frozenset(
    {
        "session_id",
        "turn_id",
        "invocation_id",
        "execution_id",
        "task_id",
        "goal_id",
        "handoff_id",
        "customer_id",
        "ticket_id",
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
    payload = event.data
    event_type = str(event.event_type or "").strip()
    if event_type == "chat.phase_timing":
        return _chat_phase_metrics(payload)
    if event_type in {"llm.call.completed", "llm.call.failed", "llm_call"}:
        return _model_provider_metrics(payload)
    if event_type.startswith("agent.invocation."):
        return _lifecycle_metrics(payload, family="invocation", terminal=event_type)
    if event_type.startswith("agent.execution."):
        return _lifecycle_metrics(payload, family="execution", terminal=event_type)
    if event_type.startswith("agent.turn."):
        return _lifecycle_metrics(payload, family="turn", terminal=event_type)
    if event_type.startswith("agent.handoff."):
        return _lifecycle_metrics(payload, family="handoff", terminal=event_type)
    if event_type == "policy.decision":
        return _policy_metrics(payload)
    if event_type == "safety.preempted":
        return _safety_metrics(payload)
    if event_type == "business.outcome.recorded":
        return _business_metrics(payload)
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


def generic_metric_projection(event: TelemetryEvent) -> tuple[str, str, float]:
    payload = event.data
    for key in _GENERIC_METRIC_VALUE_KEYS:
        try:
            return (
                _GENERIC_METRIC_KINDS.get(event.event_type, _KIND_GAUGE),
                "1",
                float(payload[key]),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return _GENERIC_METRIC_KINDS.get(event.event_type, _KIND_GAUGE), "1", 1.0


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
    _append_metric(
        metrics,
        "openminion_provider_token_ttft_ms",
        _KIND_HISTOGRAM,
        payload.get("provider_token_ttft_ms"),
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


def _model_identity_attributes(payload: dict[str, Any]) -> dict[str, str]:
    attributes = {}
    provider = str(payload.get("provider") or "").strip()
    model = str(payload.get("model") or "").strip()
    if provider:
        attributes["gen_ai.provider.name"] = _bounded_label(provider, default="unknown")
    if model:
        attributes["gen_ai.response.model"] = _bounded_label(model, default="unknown")
    return attributes


def _append_model_cost_metric(
    metrics: list[dict[str, Any]], payload: dict[str, Any]
) -> None:
    cost_source = str(payload.get("cost_source") or "").strip()
    if not cost_source:
        return
    attributes = {
        "cost_source": _bounded_label(cost_source, default="unknown"),
        **_model_identity_attributes(payload),
    }
    _append_metric(
        metrics,
        "openminion_model_cost",
        _KIND_COUNTER,
        payload.get("cost_usd"),
        attributes,
        unit="USD",
    )


def _model_provider_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    common = {
        "transport": _bounded_label(
            payload.get("transport") or "runtime", default="runtime"
        ),
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
    usage = payload.get("usage")
    usage_map = usage if isinstance(usage, dict) else {}
    token_identity = _model_identity_attributes(payload)
    for token_type, keys in (
        ("input", ("input_tokens", "prompt_tokens")),
        ("output", ("output_tokens", "completion_tokens")),
        ("cache_read", ("cached_tokens", "cache_read_tokens")),
        ("cache_write", ("cache_creation_tokens", "cache_write_tokens")),
    ):
        value = _first_present(usage_map, *keys)
        _append_metric(
            metrics,
            "gen_ai.client.token.usage",
            _KIND_HISTOGRAM,
            value,
            {**token_identity, "gen_ai.token.type": token_type},
            unit="{token}",
        )
    duration_ms = _first_present(
        payload,
        "provider_round_trip_ms",
        "round_trip_ms",
        "latency_ms",
        "elapsed_ms",
    )
    duration_seconds = (
        float(duration_ms) / 1000.0 if isinstance(duration_ms, (int, float)) else None
    )
    _append_metric(
        metrics,
        "gen_ai.client.operation.duration",
        _KIND_HISTOGRAM,
        duration_seconds,
        {
            "transport": common["transport"],
            "profile_kind": common["profile_kind"],
            "outcome": common["outcome"],
        },
        unit="s",
    )
    for metric_name, payload_key in (
        ("openminion_model_request_bytes", "request_bytes"),
        ("openminion_model_response_bytes", "response_bytes"),
        ("openminion_context_bytes", "context_bytes"),
        ("openminion_context_tokens", "context_tokens"),
        ("openminion_context_segment_count", "context_segment_count"),
        ("openminion_tool_schema_bytes", "tool_schema_bytes"),
        ("openminion_tool_schema_count", "tool_schema_count"),
        ("openminion_exposed_tool_count", "exposed_tool_count"),
    ):
        value = payload.get(payload_key)
        if value is None:
            value = usage_map.get(payload_key)
        _append_metric(metrics, metric_name, _KIND_HISTOGRAM, value, common)
    _append_model_cost_metric(metrics, payload)
    return metrics


def _lifecycle_metrics(
    payload: dict[str, Any],
    *,
    family: str,
    terminal: str,
) -> list[dict[str, Any]]:
    if terminal.endswith(".started"):
        return []
    outcome = _outcome_label(payload)
    metrics: list[dict[str, Any]] = []
    _append_metric(
        metrics,
        f"openminion_{family}_operations_total",
        _KIND_COUNTER,
        1,
        {"segment_family": family, "outcome": outcome},
        unit="{operation}",
    )
    _append_metric(
        metrics,
        f"openminion_{family}_duration_ms",
        _KIND_HISTOGRAM,
        payload.get("duration_ms"),
        {"segment_family": family, "outcome": outcome},
        unit="ms",
    )
    return metrics


def _policy_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    _append_metric(
        metrics,
        "openminion_policy_decisions_total",
        _KIND_COUNTER,
        1,
        {
            "decision": _bounded_label(payload.get("decision"), default="unknown"),
            "operation": _bounded_label(payload.get("action"), default="unknown"),
        },
        unit="{decision}",
    )
    return metrics


def _safety_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    _append_metric(
        metrics,
        "openminion_safety_preemptions_total",
        _KIND_COUNTER,
        1,
        {
            "operation": _bounded_label(payload.get("action"), default="unknown"),
            "violation_category": _bounded_label(
                payload.get("violation_category"), default="unknown"
            ),
        },
        unit="{preemption}",
    )
    return metrics


def _business_metrics(payload: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    common = {
        "business_domain": _bounded_label(payload.get("domain"), default="unknown"),
        "outcome": _outcome_label(payload),
    }
    _append_metric(
        metrics,
        "openminion_business_outcomes_total",
        _KIND_COUNTER,
        1,
        common,
        unit="{outcome}",
    )
    _append_metric(
        metrics,
        "openminion_business_outcome_value",
        _KIND_HISTOGRAM,
        payload.get("value"),
        common,
        unit=str(payload.get("unit") or "1")[:16],
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
    *,
    unit: str | None = None,
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
            "unit": unit or _unit_for_metric(name),
        }
    )


def _unit_for_metric(name: str) -> str:
    if name.endswith("_ms"):
        return "ms"
    if name.endswith("_bytes"):
        return "By"
    if name.endswith("_tokens"):
        return "{token}"
    if name.endswith("_total"):
        return "{event}"
    return "1"


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
