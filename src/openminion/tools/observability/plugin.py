from functools import partial

from openminion.tools.ops.specialized import make_handler

from . import args

_observability_handler = partial(make_handler, "observability")
_h_prometheus_rules = _observability_handler(
    "prometheus_rules", args.PrometheusRulesArgs
)
_h_prometheus_alerts = _observability_handler(
    "prometheus_alerts", args.PrometheusAlertsArgs
)
_h_prometheus_query = _observability_handler(
    "prometheus_query", args.PrometheusQueryArgs
)
_h_otel_trace = _observability_handler("otel_trace", args.TraceLookupArgs)
