# `services/lifecycle/`

Owner: services-layer
Pairs with: standalone (no `modules/lifecycle/`)

## Purpose

Runtime lifecycle helpers grouped by runtime role per the
`services/README.md` archetypes section:

- Per-turn orchestration entry point (`request_orchestrator.run_turn`)
- Self-improvement engine that records improvement notes across runs
- Sidecar lifecycle management — declaring, starting, and consenting
  to long-running helper processes (e.g. `pinchtab`)

## Public surface

Currently exported through direct submodule imports (no `__init__.py`
re-exports — consumers import by file). The intended public surface:

- `request_orchestrator.run_turn(...)` — top-level per-turn entry
- `self_improvement.ImprovementNote` — record type
- `self_improvement.SelfImprovementEngine` — engine class
- `sidecars.SidecarSpec` — sidecar declaration record
- `sidecars.SidecarManager` — runtime manager (single per runtime)
- `sidecars.SidecarConsent`, `sidecars.SidecarConsentStore` — consent
  surface
- `sidecars.SidecarExecutor` (Protocol), `sidecars.SubprocessExecutor`,
  `sidecars.ToolExecExecutor`
- `sidecars.SidecarAdapter` (Protocol), `sidecars.PinchTabSidecarAdapter`
- `sidecars.default_sidecar_manager(...)` — canonical builder
- `sidecars.ensure_pinchtab_autostart(...)`,
  `sidecars.ensure_sidecar_autostart(...)`,
  `sidecars.ensure_sidecars_autostart(...)` — startup helpers

## Owned objects

- `SidecarManager` runtime instance.
- `SelfImprovementEngine` instance (when enabled).
- `SidecarConsentStore` (operator consent records).
- Long-running sidecar processes spawned via the executors.

## Non-goals

- The turn-flow logic itself — `run_turn` is composition glue; the
  flow lives in `services/agent/execution/`.
- Improvement-note storage schema — owned by `modules/storage/`.
- Specific sidecar binaries — `pinchtab` and similar live outside
  this package; only the adapter wiring is here.

## Dependencies

- `services/agent/` — turn flow.
- `services/runtime/` — runtime composition for sidecar startup.
- `modules/storage/` — improvement-note + consent persistence.
- `base/config/` — operator-tunable sidecar enables.

## How this differs from `modules/`

Lifecycle is a runtime-only concern (process management, per-turn
orchestration, self-improvement loops that observe live runs).
Modules have no symmetric "lifecycle/" — anything that needs a long-
running process or a per-turn coordinator lives here.
