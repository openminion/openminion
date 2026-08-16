# Telemetry export quickstarts

OpenMinion writes telemetry locally first. External export is optional and uses
OTLP. Install the exporter dependencies with `pip install openminion[otel]`,
then configure `runtime.telemetry_exporter` in the normal OpenMinion JSON
configuration.

The safest production topology is OpenMinion to an OpenTelemetry Collector,
then the Collector to a backend. That gives each signal its correct endpoint,
keeps credentials out of OpenMinion configuration, and lets a trace-only
backend reject neither log nor metric flushes.

All examples below keep content export disabled. Review your backend's access,
retention, and data-processing policy before enabling any input, output, tool,
or assistant-body field. `telemetryctl doctor --live-export` proves local
recording and OTLP transport acceptance only. It does not prove Collector
receipt or vendor visibility.

## Generic OTLP or OpenTelemetry Collector

Use a Collector or backend that accepts traces, metrics, and logs on one gRPC
listener:

```json
{
  "runtime": {
    "telemetry_exporter": {
      "enabled": true,
      "endpoint": "http://<OTLP_HOST>:4317",
      "protocol": "grpc",
      "service_name": "openminion",
      "sample_rate": 1.0
    }
  }
}
```

For OTLP/HTTP use protocol `http/protobuf` and the receiver's base URL, such as
`http://<OTLP_HOST>:4318`. OpenMinion derives the standard `/v1/traces`,
`/v1/metrics`, and `/v1/logs` paths for each signal. A configured URL already
ending in one of those standard paths is rebased to the other signal paths.
OpenTelemetry defines ports 4317 for gRPC and 4318 for HTTP by default.
Search by `service.name`, `openminion.invocation_id`,
`openminion.execution_id`, or `openminion.event_type`.

Checked 2026-08-11: [OpenTelemetry Python exporters](https://opentelemetry.io/docs/languages/python/exporters/)
and [OTLP exporter specification](https://opentelemetry.io/docs/specs/otel/protocol/exporter/).

## Jaeger

Jaeger v2 exposes OTLP/gRPC on 4317 and OTLP/HTTP on 4318. Point a Collector's
trace pipeline at Jaeger and keep OpenMinion pointed at the Collector using the
generic configuration above. In Jaeger, search for service `openminion`, then
filter span tags such as `openminion.invocation_id` and
`openminion.event_type`.

Jaeger is a trace backend. A successful OpenMinion live-export probe is a log
signal, so Jaeger UI visibility is separate and should be verified with a real
invocation trace, not inferred from the probe.

Checked 2026-08-11: [Jaeger 2.20 configuration](https://www.jaegertracing.io/docs/2.20/deployment/configuration/).

## Grafana Tempo

Tempo accepts OTLP/gRPC on 4317 and, when enabled, OTLP/HTTP on 4318. Use a
Collector trace pipeline for Tempo and the generic OpenMinion-to-Collector
configuration. For a multi-tenant deployment, configure the tenant header in
the Collector exporter with a placeholder such as `<TENANT_ID>`.

In Grafana Explore, search Tempo with `resource.service.name = "openminion"`
and narrow by `openminion.invocation_id`, `openminion.execution_id`, or
`openminion.event_type`. Tempo is trace-focused; Collector/probe receipt and
Tempo trace visibility are distinct proof levels.

Checked 2026-08-11: [Grafana Tempo OTLP receiver and Collector guide](https://grafana.com/docs/tempo/latest/set-up-for-tracing/instrument-send/set-up-collector/otel-collector/)
and [pushing spans](https://grafana.com/docs/tempo/latest/api_docs/pushing-spans-with-http/).

## Langfuse

Langfuse accepts OTLP/HTTP traces at the deployment base followed by
`/api/public/otel/v1/traces`. Authentication uses project credentials via
Basic Auth. Put placeholder credentials such as `<PUBLIC_ID>` and
`<PRIVATE_SECRET>` in a Collector secret store, not in committed OpenMinion
configuration, then export only the Collector trace pipeline to Langfuse.

Search Langfuse observations by service name and the OpenMinion correlation
attributes. Langfuse trace ingestion does not establish receipt of the
OpenMinion log probe, and its UI is vendor-level evidence requiring a separate
authorized check.

Checked 2026-08-11: [Langfuse compatibility matrix](https://langfuse.com/docs/compatibility)
and [Langfuse public API and OTLP ingestion](https://langfuse.com/docs/api-and-data-platform/features/public-api).

## Arize Phoenix

Self-hosted Phoenix accepts OTLP/HTTP protobuf traces on the application HTTP
port at `/v1/traces` and OTLP/gRPC on 4317. Phoenix Cloud currently documents
HTTP trace ingestion. Route a Collector trace pipeline to the appropriate
Phoenix endpoint and keep any authorization value as `<PHOENIX_CREDENTIAL>` in
the Collector's secret mechanism.

Search the Phoenix trace table by project/service, then inspect
`openminion.invocation_id`, `openminion.execution_id`, and model attributes.
Phoenix trace visibility is vendor evidence; it is not implied by local or
Collector transport proof.

Checked 2026-08-11: [Phoenix self-hosted ports](https://arize.com/docs/phoenix/self-hosting/configuration)
and [Phoenix endpoint guidance](https://arize.com/docs/phoenix/learn/faqs/what-is-my-phoenix-endpoint).

## Pydantic Logfire

Logfire is OpenTelemetry-compatible. Use its project-specific OTLP settings
through an OpenTelemetry Collector, store the credential as
`<LOGFIRE_CREDENTIAL>`, and keep OpenMinion configured only for the Collector.
This avoids embedding a hosted endpoint or authorization metadata in local
debug output and lets the Collector route each signal correctly.

Search Logfire by service `openminion` and the structural OpenMinion
correlation attributes. Treat Logfire UI visibility as separate vendor proof.

Checked 2026-08-11: [Logfire OpenTelemetry integrations](https://logfire.pydantic.dev/docs/integrations/)
and [Logfire alternative backend guidance](https://logfire.pydantic.dev/docs/how-to-guides/alternative-backends/).

## Verification checklist

1. Run `telemetryctl doctor` and confirm the exporter is enabled and its
   endpoint is configured; the command does not print endpoint or header
   values in new summary surfaces.
2. Run `telemetryctl doctor --live-export`. Exit 0 proves one local probe row
   and one accepted, flushed OTLP log transport. It does not prove backend
   ingestion.
3. Run a real invocation and use `telemetryctl debug latest` to copy its safe
   correlation fields.
4. Confirm Collector receipt, then separately search the backend using the
   fields listed above. Record those as different evidence levels.
