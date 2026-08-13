from __future__ import annotations

from datetime import UTC, datetime

from openminion.base.config import OTELExporterConfig

from ..schemas import TelemetryDebugDiagnostic, TelemetryDebugExportHealth


def build_debug_export_health(
    config: OTELExporterConfig,
    diagnostics: list[TelemetryDebugDiagnostic],
    *,
    live_queue_stats: dict[str, int] | None = None,
) -> TelemetryDebugExportHealth:
    enabled = bool(config.enabled)
    endpoint_configured = bool(str(config.endpoint or "").strip())
    raw_protocol = str(config.protocol or "").strip().lower()
    protocol = "http/protobuf" if raw_protocol == "http" else raw_protocol or None
    if protocol not in {"http/protobuf", "grpc", None}:
        protocol = None
    if not enabled:
        state, source = "disabled", "disabled"
    elif not endpoint_configured or protocol is None:
        state, source = "incomplete", "no_runtime_connection"
        if endpoint_configured and protocol is None:
            diagnostics.append(
                TelemetryDebugDiagnostic("UNKNOWN_EXPORT_PROTOCOL", "warning")
            )
    elif live_queue_stats is not None:
        drops = int(live_queue_stats.get("drops", 0))
        failures = int(live_queue_stats.get("flush_failures", 0))
        state = "error" if drops or failures else "ready"
        source = "in_process"
    else:
        state, source = "unavailable", "no_runtime_connection"
        diagnostics.append(
            TelemetryDebugDiagnostic("QUEUE_LIVE_STATE_UNAVAILABLE", "info")
        )
    queue = _queue_health(source, live_queue_stats)
    return TelemetryDebugExportHealth(
        state=state,
        enabled=enabled,
        endpoint_configured=endpoint_configured,
        protocol=protocol,
        sampling_rate=float(config.sample_rate),
        queue=queue,
    )


def _queue_health(
    source: str,
    stats: dict[str, int] | None,
) -> dict[str, object]:
    if source != "in_process" or stats is None:
        return {
            "capacity": None,
            "depth": None,
            "drops": None,
            "flush_failures": None,
            "source": source,
            "observed_at": None,
            "freshness": "unavailable",
        }
    return {
        "capacity": int(stats.get("queue_capacity", 0)),
        "depth": int(stats.get("queue_depth", 0)),
        "drops": int(stats.get("drops", 0)),
        "flush_failures": int(stats.get("flush_failures", 0)),
        "source": source,
        "observed_at": datetime.now(tz=UTC).isoformat(),
        "freshness": "live",
    }


__all__ = ["build_debug_export_health"]
