# `modules/policy/`

Owner: `openminion-policy`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

Runtime policy enforcement: per-tool/per-invocation decisions about
whether the agent may execute an action, and the persistent grant store
that records standing approvals. Owns the typed decision/grant
contracts and the SQLite-backed policy store.

## Scope

- `PolicyCtl` service + `PolicyCtlInterface` Protocol
- Decision schema: `PolicyDecision`, `PolicyGrant`, `PolicyGrantInput`
- Risk specification (`RiskSpec`) and invocation summarization
- Tool-invocation policy hooks (`PolicyToolHook`)
- Argument sanitization and stable invocation hashing helpers
- Persistent grant store (`SQLitePolicyStore`)

## Non-goals

- High-level approval UX (handled by CLI/TUI surfaces)
- Cross-process policy distribution
- Identity verification (lives in `modules/identity/`)

## Public surface

Re-exported from `openminion.modules.policy`:

- Service: `PolicyCtl`, `PolicyCtlInterface`, `PolicyToolHook`,
  `PolicyConfig`
- Decisions / grants: `PolicyDecision`, `PolicyGrant`,
  `PolicyGrantInput`, `RiskSpec`
- Context: `ContextSummary`, `InvocationSummary`
- Helpers: `sanitize_args`, `stable_invocation_hash`
- Storage: `SQLitePolicyStore`
- Versioning: `POLICY_INTERFACE_VERSION`,
  `ensure_policy_compatibility`

## Dependencies

- `modules/tool/` — for tool invocation contracts
- `modules/storage/` — SQLite substrate
- `base/` — config, runtime, errors

## Canonical shape

Canonical with `interfaces.py`, `models.py`, `runtime/` subpackage,
`storage/` subpackage, `cli.py`. The argument-sanitization rules and
stable hash function are the canonical owners — other modules should
not reimplement them.
