# Services Layer Overview

## Purpose

`openminion.services` owns runtime wiring: composition, bootstrap, transport
integration, supervision, and long-running orchestration that stitches the
module layer into runnable system behavior.

## Ownership split with `modules/`

## Canonical package archetypes

### Same-name orchestration peers

Four service subpackages pair with a same-named feature area in
`openminion.modules` and handle that area's runtime integration:

- `brain/`
- `context/`
- `identity/`
- `tool/`

### Runtime concern packages

Eight service subpackages are standalone runtime concerns with no
`modules/` peer:

- `agent/`
- `channel/`
- `cron/`
- `gateway/`
- `health/`
- `runtime/`
- `security/`
- `supervision/`

`services/` does not mirror `modules/` one-to-one. Module-only owners such as
`llm/`, `memory/`, `session/`, `storage/`, and `skill/` stay in the modules
layer. Runtime glue like `services/integration/skill_harness.py` is not a
subsystem peer of `modules/skill/`.

### Grouped helper packages

Smaller support owners that are not standalone subsystem peers are grouped by
runtime role:

- `bootstrap/`: startup config bootstrap, onboarding, data-root migration, and shared path helpers
- `lifecycle/`: turn orchestration, self-improvement, and sidecar lifecycle management
- `diagnostics/`: debug registry and owner-status reporting
- `integration/`: cross-module verification and vector-sync bridges

Only `config.py` and `constants.py` remain as root-level `.py` files.

## Naming notes

- `runtime/` here means cross-owner system orchestration, distinct from
  `openminion.base.runtime` and from `<module>/runtime/`; see
  `openminion/src/openminion/modules/README.md` for the canonical module-local
  rule.

## What belongs here

- Runtime composition and orchestration
- Transport-facing glue used by API, CLI, daemon, or gateway surfaces
- Supervision, lifecycle, health, diagnostics, and integration helpers

## What does not belong here

- Feature schemas, domain contracts, and provider adapters that belong to a
  specific module owner
- New ad hoc flat files at `services/` root
- Direct feature ownership that should live in `openminion.modules.*`
