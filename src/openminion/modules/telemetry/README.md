# Telemetry Module

Owner: `openminion-telemetry`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

This module owns telemetry adapters, service APIs, trace layout, lifecycle hooks, telemetry persistence, and OpenTelemetry export. Primary contracts: `interfaces.py`, `schemas.py`, `service.py`, `adapter.py`, and `export/otel.py`. Typed telemetry payloads live in `schemas.py` and module event helpers.

## OpenTelemetry export

OpenTelemetry export is additive. Local telemetry persistence remains the
first write path; OTLP export is enabled only when
`runtime.telemetry_exporter.enabled=true` and a non-empty
`runtime.telemetry_exporter.endpoint` is configured.

Typed config fields:

- `enabled`
- `endpoint`
- `service_name`
- `protocol` (`http/protobuf` or `grpc`)
- `include_assistant_body` (default `false`)
- `sample_rate` (deterministic by trace key)

Current exporter coverage:

1. LLM calls emit paired spans with `gen_ai.*` semantic-convention
   attributes when start/completion events are available.
2. Selected storage, memory, module, and cache events map to metrics or
   explicit spans; generic catch-all events remain log records.
3. Hosted-backend adapters such as Logfire delegate through the same
   OTel exporter rather than owning parallel telemetry formats.

Ownership note:

1. `openminion/src/openminion/modules/telemetry/config.py` is a thin delegate
   to the shared runtime config owner. The canonical source of telemetry
   exporter settings remains `runtime.telemetry_exporter` in the unified
   OpenMinion config shape.

Operator notes:

1. Install the optional extras with `pip install openminion[otel]`.
2. Body/content fields are excluded from exported attributes by default.
   Set `include_assistant_body=true` only when the collector boundary is
   trusted and the additional exposure is intentional.
3. List and tuple payloads are exported as compact deterministic JSON strings
   at their original attribute keys so multi-value payloads stay unambiguous.
4. OTel export is provider-neutral. LangSmith, Helicone, Arize, Datadog,
   Jaeger, and similar collectors should integrate through their OTel
   ingestion path rather than via bespoke openminion adapters.
