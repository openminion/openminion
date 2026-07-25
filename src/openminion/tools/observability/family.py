from __future__ import annotations

from openminion.modules.tool.exposure import ToolExposureProfile
from openminion.modules.tool.framework import ToolDecl, ToolFamilySpec

from .args import (
    PrometheusRulesArgs,
    PrometheusAlertsArgs,
    PrometheusQueryArgs,
    TraceLookupArgs,
)
from .interfaces import (
    ALL_OBSERVABILITY_TOOLS,
    TOOL_OBSERVABILITY_PROM_RULES,
    TOOL_OBSERVABILITY_PROM_ALERTS,
    TOOL_OBSERVABILITY_PROM_QUERY,
    TOOL_OBSERVABILITY_TRACE_LOOKUP,
)
from .plugin import (
    _h_prometheus_rules,
    _h_prometheus_alerts,
    _h_prometheus_query,
    _h_otel_trace,
)

OBSERVABILITY_FAMILY = ToolFamilySpec(
    module_id="observability",
    min_scope_default="READ_ONLY",
    common_tags=("plugin", "ops", "observability"),
    exposure_profiles=(
        ToolExposureProfile(
            profile_id="observability_readonly",
            title="Observability",
            summary="Read-only Prometheus and OpenTelemetry inspection.",
            tool_names=frozenset(ALL_OBSERVABILITY_TOOLS),
            target_kinds=frozenset({"ops-target"}),
            dependencies=frozenset({"fixture:observability"}),
            evidence_expectations=("return fixture/live source and evidence digest",),
            stop_rules=("stop before mutation or unscoped provider access",),
            guidance_names=("ops.safety.v1",),
            activation_hint="Activate explicitly for a scoped read-only operations task.",
        ),
    ),
    tools=(
        ToolDecl(
            TOOL_OBSERVABILITY_PROM_RULES,
            PrometheusRulesArgs,
            _h_prometheus_rules,
            "Inspect Prometheus rule definitions with source citations.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_OBSERVABILITY_PROM_ALERTS,
            PrometheusAlertsArgs,
            _h_prometheus_alerts,
            "Inspect active Prometheus alerts in a bounded time window.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_OBSERVABILITY_PROM_QUERY,
            PrometheusQueryArgs,
            _h_prometheus_query,
            "Run a bounded read-only Prometheus query fixture.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
        ToolDecl(
            TOOL_OBSERVABILITY_TRACE_LOOKUP,
            TraceLookupArgs,
            _h_otel_trace,
            "Lookup an OpenTelemetry trace by trace id.",
            idempotent=True,
            tags=("read_only",),
            capabilities=("read_only", "ops", "evidence"),
        ),
    ),
)

__all__ = ["OBSERVABILITY_FAMILY"]
