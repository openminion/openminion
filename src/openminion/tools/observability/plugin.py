from openminion.tools.ops.specialized import make_handler

from .args import (
    PrometheusRulesArgs,
    PrometheusAlertsArgs,
    PrometheusQueryArgs,
    TraceLookupArgs,
)

_h_prometheus_rules = make_handler(
    "observability", "prometheus_rules", PrometheusRulesArgs
)
_h_prometheus_alerts = make_handler(
    "observability", "prometheus_alerts", PrometheusAlertsArgs
)
_h_prometheus_query = make_handler(
    "observability", "prometheus_query", PrometheusQueryArgs
)
_h_otel_trace = make_handler("observability", "otel_trace", TraceLookupArgs)
