# `services/diagnostics/`

Owner: services-layer
Pairs with: standalone (no `modules/` peer)

## Purpose

Operator-facing diagnostics surface. Owns the debug-payload registry
that surfaces tool-selection internals on demand and the owner-status
reporter that walks the composed runtime and emits a per-owner
status snapshot. Consumed by interactive CLI debug commands and by
the health snapshot.

## Public surface

Currently exported through direct submodule imports (no `__init__.py`
re-exports — consumers import by file). The intended public surface:

- `debug.ToolSelectionDebugPayload` — tool-selection debug record
- `debug.create_tool_selection_debug_payload(...)` — builder
- `debug.load_debug_providers()` — registers built-in debug providers
- `debug.is_debug_surface_enabled(...)` — operator gate check
- `owner_status.build_owner_status(...)` — single owner-status
  snapshot builder consumed by the interactive CLI and health surfaces

## Owned objects

- Module-level debug provider registry (singleton at runtime).
- Per-call `ToolSelectionDebugPayload` snapshots.

## Non-goals

- Health probing — that is `services/health/`. Owner-status is a
  separate concern (who is registered, what is wired) from
  health (is each owner currently working).
- Telemetry emission — owned by `modules/telemetry/`.
- Log formatting — operator-facing logging happens through
  stdlib `logging` configured by `base/config/`.

## Dependencies

- `services/runtime/composition.py` — owner walking.
- `modules/tool/` — tool-selection internals exposed by the debug
  payload.

## How this differs from `modules/`

Diagnostics is a pure operator-facing concern and lives entirely in
the services layer. Modules raise events; diagnostics composes them
into operator-readable reports.
