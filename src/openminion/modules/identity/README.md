# `modules/identity/`

Owner: `openminion-identity`
Shape: `template-aligned`
Runtime peer: paired with `openminion.services.identity`

## Purpose

Owns the agent's identity surface: persistent agent profile (name,
description, capabilities, posture), the snippet-renderer that produces
identity prefixes for context packs, and the budget-aware render rules
that govern how much identity material each turn-purpose receives.

## Scope

- `AgentProfile` record + storage backends (in-memory, SQLite)
- `IdentityCtl` service + `IdentityCtlInterface` Protocol
- Snippet rendering and render-budget rules (`runtime/`)
- Identity-bundle import/export

## Non-goals

- Cross-agent authentication or capability-grant policy (lives in
  `modules/policy/`)
- Public-facing UX for identity editing
- Cross-process identity sync

## Public surface

Re-exported from `openminion.modules.identity`:

- `AgentProfile`, `IdentitySnippet`
- `IdentityBundle`, `IdentityDocument`, `load_identity_bundle`
- `IdentityCtl`, `IdentityCtlInterface`
- Stores: `InMemoryIdentityStore`, `SQLiteIdentityStore`
- Versioning: `IDENTITY_INTERFACE_VERSION`,
  `ensure_identity_compatibility`

## Dependencies

- `modules/storage/` — SQLite store substrate
- `services/identity/` — bootstrapping helpers (paired peer)
- `base/` — config, paths

## Canonical shape

Canonical with `interfaces.py`, `models.py`, `runtime/` subpackage,
`storage/` subpackage, `cli.py`. The render-budget surface was
rebaselined in the IROR lane (closed) — `RenderingConfig.default_budgets`
in `config.py` is the canonical owner of per-purpose budget values.
