# `services/health/`

Owner: services-layer
Pairs with: standalone (no `modules/` peer)
Canonical entry: `collect_health_snapshot(...)`

## Purpose

Operator-facing readiness and liveness reporting. Owns the runtime
probe registry (config exists, storage ready, provider supported,
provider key valid, channels enabled, plugins enabled, runtime
bootstrap reached) and the snapshot assembler that composes those
probes into a single `HealthSnapshot` record for the daemon's health
endpoint.

## Public surface

Currently exported through direct submodule imports (no `__init__.py`
re-exports — consumers import by file). The intended public surface:

- `service.collect_health_snapshot(...)` — single canonical snapshot
  entry consumed by the daemon.
- `types.HealthCheckResult`, `types.HealthSnapshot`,
  `types.HealthCheck`, `types.LifecycleFact` — typed records.
- `probes.ProbeResult`, `probes.StorageProbeResult` — probe records.
- Probe functions: `probe_config_exists`, `probe_storage_ready`,
  `probe_provider_supported`, `probe_provider_key`,
  `probe_provider_session`, `probe_channels_enabled`,
  `probe_default_channel_in_enabled`, `probe_plugins_enabled`,
  `probe_runtime_bootstrap`.
- `snapshot.py` — snapshot assembly helpers.
- `lifecycle.py` — lifecycle-fact emitter consumed by the snapshot.
- `observability.py` — telemetry bridge for health events.
- `reporting.py` — operator-facing health summary formatting.

## Owned objects

- Per-call `HealthSnapshot` records (no long-lived state).
- Lifecycle facts attached to the runtime.

## Non-goals

- Distinct `/readyz` vs `/livez` endpoints — currently a single
  snapshot endpoint; splitting them is on the operational-readiness
  backlog.
- Restart / supervision policy — owned by `services/supervision/`.
- Per-module health implementation — each module owns its own
  internal health probes; this package only composes their results.

## Dependencies

- `base/config/` — config presence check.
- `modules/storage/` — storage ready probe.
- `modules/llm/` — provider checks.
- `services/runtime/` — runtime bootstrap probe.

## How this differs from `modules/`

There is no `modules/health/`. Health is purely a runtime / operator
concern. Modules expose internal probes; this package composes them
into a single snapshot.
