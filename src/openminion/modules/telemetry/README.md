# Telemetry Module

Owner: `openminion-telemetry`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

This module owns telemetry adapters, service APIs, trace layout, lifecycle hooks, telemetry persistence, and OpenTelemetry export. Primary contracts: `interfaces.py`, `schemas.py`, `service.py`, `adapter.py`, and `export/otel.py`. Typed telemetry payloads live in `schemas.py` and module event helpers.

`TelemetryService` persists canonical events locally before calling the
`TelemetryExporter` protocol in `interfaces.py`. `OpenTelemetryTraceExporter`
is the default external adapter, and another external backend can implement the
same three-method contract without changing local storage or event schemas.
External exporter implementations own their failure handling; the service does
not add a second exception-suppression layer around them.

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
- `include_input_messages`, `include_output_messages`, and
  `include_tool_content` (independent, default `false`)
- `include_local_content` (independent local persistence control, default
  `false`)
- `sample_rate` (deterministic by trace key)

For OTLP/HTTP, configure one receiver base URL. The exporter derives the
standard `/v1/traces`, `/v1/metrics`, and `/v1/logs` signal paths. OTLP/gRPC
uses the configured endpoint unchanged for all three signals. Unsupported
protocol values disable external export and produce a warning rather than
silently selecting another transport.

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
   The compatibility flag `include_assistant_body=true` enables output
   messages only. Input messages and tool content require their separate
   controls.
3. List and tuple payloads are exported as compact deterministic JSON strings
   at their original attribute keys so multi-value payloads stay unambiguous.
4. OTel export is provider-neutral. LangSmith, Helicone, Arize, Datadog,
   Jaeger, and similar collectors should integrate through their OTel
   ingestion path rather than via bespoke openminion adapters.
5. See [Telemetry export quickstarts](../../../../docs/telemetry-export-quickstarts.md)
   for Collector, Jaeger, Tempo, Langfuse, Phoenix, Logfire, and generic OTLP
   setup with explicit proof and privacy boundaries.

## Operator inspection CLI

The standalone `telemetryctl` surface is JSON-first and intended for local
debugging without introducing a dashboard dependency or runtime semantic
analysis. It reports structural telemetry facts only.

Useful commands:

1. `telemetryctl doctor` reports an overall `ready` or `attention` status plus
   database, trace-root, and OpenTelemetry exporter readiness. A disabled
   exporter is valid; an enabled exporter without an endpoint is `incomplete`.
2. `telemetryctl catalog` prints registered event types with their OTel export
   disposition (`span`, `metric`, `log`, or `excluded`).
3. `telemetryctl trace list` lists recent LLM trace artifacts under
   `<data_root>/traces/llm/`.
4. `telemetryctl trace show <relative-path>` prints structural file metadata
   while enforcing a readable, non-symlinked path under the trace root. Use
   the deliberate `--raw` flag to include artifact content; releases before
   this summary-first change printed content by default.
5. `telemetryctl invocation list` lists durable invocations and legacy gaps.
6. `telemetryctl invocation show <invocation-id>` prints structural events,
   timing, token/cache/cost, policy, log, orphan, and propagation facts.
7. `telemetryctl invocation graph <invocation-id>` prints finite execution
   segments and their links without prompt or reasoning interpretation.
8. `telemetryctl debug latest|failed|invocation <id>` prints the shared
   `openminion.telemetry_debug.v1` report used by the root and interactive
   operator surfaces.

These commands deliberately avoid content classification or response-quality
judgment. Post-hoc eval/replay tooling may analyze captured traces separately,
but runtime telemetry remains operational: paths, timings, IDs, counts,
statuses, and export dispositions.
