# `services/context/`

Owner: services-layer
Pairs with: `modules/context/` (segment assembly, ranking, capsules)
Canonical builder: `services/runtime/bootstrap.py:build_session_context_service`

## Purpose

Runtime wiring for the context subsystem. Takes the segment-assembly,
budgeting, and ranking primitives from `modules/context/` and exposes
them as a runtime service that the agent and gateway can call to
materialize a session's prompt-shaped context for a turn. Also owns
the session-archive cleanup utility and the session-slice bridge that
translates session-shaped records into context-shaped segments.

## Public surface

Re-exported from `openminion.services.context`:

- `SessionContextService` — turn-time context resolver
- `resolve_session_archive_root(...)` — canonical archive-root resolver
- `ContextCtlGatewayAdapter` — gateway-facing control adapter
- `ContextBudgetConfig` — operator-tunable budget knob
- `assemble_budgeted_context(...)` — single-call budgeted assembly entry
- `SessionCleanupUtility` — archive-cleanup helper

Internal modules of note:

- `session.py` — `SessionContextService`
- `adapter.py` — `ContextCtlGatewayAdapter`
- `budget.py`, `pack/semantics.py` — budget config and budget rules
- `cleanup.py` — `SessionCleanupUtility`
- `slices.py` — session → segment translation
- `constants.py` — fixed internal names shared across the package

## Owned objects

- `SessionContextService` runtime instance.
- Session-archive root path (resolved once at service build).
- Active `ContextBudgetConfig` for the runtime.

## Non-goals

- Segment assembly internals (owned by `modules/context/`).
- Ranking weights / scoring (owned by `modules/context/`).
- Capsule storage (owned by `modules/storage/` + `modules/context/`).
- LLM rendering (owned by `modules/llm/`).

## Dependencies

- `modules/context/` — segment assembly, ranking, capsule primitives.
- `modules/session/` — session record shape.
- `modules/storage/` — capsule + summary persistence.
- `base/paths.py` — archive-root layout.

## How this differs from `modules/`

`modules/context/` owns the feature: segment models, ranking,
budgeting math, capsule schema. `services/context/` is the runtime
peer that exposes that feature as a turn-time service to the agent
and gateway, and is the only place where archive-cleanup and gateway
control adapters live.
