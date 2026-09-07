# `modules/session/`

Owner: `openminion-session`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

Conversation-session lifecycle: opening / resuming / closing sessions,
persisting turn history, slice-limit enforcement, and the cron-driven
inbox/outbox queues that record asynchronous session events. Owns the
typed session record schema and SQLite + Postgres backing stores.

## Scope

- `SessionStoreAPI` and `SessionContextClientAPI` Protocols
- Backends: `SQLiteSessionStore`, `PostgresSessionStore`
- Cron-integrated session store (`storage/cron_store.py`)
- Replay/resume runtime helpers, including `SessionContinuationService`
- Slice limits (`SliceLimits`)
- Versioned compatibility check

## Non-goals

- Cross-agent message routing (lives in `modules/controlplane/`)
- Memory persistence (lives in `modules/memory/`)
- Cron scheduling primitives (lives in `services/cron/`)

## Public surface

Re-exported from `openminion.modules.session`:

- Protocols: `SessionStoreAPI`, `SessionContextClientAPI`
- Backends: `SQLiteSessionStore`, `PostgresSessionStore`
- Helpers: `SessionContinuationService`, `build_module_session_store`,
  `SliceLimits`
- Versioning: `SESSION_INTERFACE_VERSION`,
  `ensure_session_component_compatibility`

## Dependencies

- `services/cron.*` for scheduling and wakeup
- `modules/storage/` — SQLite + Postgres substrates
- `base/` — config, paths

## Canonical shape

Canonical with `interfaces.py`, `runtime/` subpackage, `storage/`
subpackage, `cli.py`. No `schemas.py` or `models.py` exists at root; typed
records live under `storage/` and `runtime/` as service-shaped owners.
