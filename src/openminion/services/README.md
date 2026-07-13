# Services Layer Overview

## Purpose

`openminion.services` owns runtime wiring: composition, bootstrap, transport
integration, supervision, and long-running orchestration that stitches the
module layer into runnable system behavior.

## Ownership split with `modules/`

## Canonical package archetypes

### Same-name orchestration peers

Three service subpackages pair with a same-named feature area in
`openminion.modules` and handle that area's runtime integration:

- `brain/`
- `context/`
- `identity/`

Changes in these paired owners must keep the module/service split explicit:
`modules/<name>/` owns domain contracts, schemas, storage-facing engines, and
provider adapters; `services/<name>/` owns runtime assembly, lifecycle, policy
composition, and cross-module wiring. A source move across the boundary needs an
owner tracker row, focused behavior tests, and an import-boundary validator note
before it lands.

### Runtime concern packages

Six service subpackages are standalone runtime concerns with no
`modules/` peer:

- `agent/`
- `cron/`
- `gateway/`
- `health/`
- `runtime/`
- `supervision/`

`services/` does not mirror `modules/` one-to-one. Module-only owners such as
`llm/`, `memory/`, `session/`, `storage/`, and `skill/` stay in the modules
layer. Policy, tool-selection, channel-policy, and stats behavior lives under
its canonical module owner. Remaining service paths for those areas are
compatibility imports or runtime wiring, not parallel feature owners.

### Grouped helper packages

Smaller support owners that are not standalone subsystem peers are grouped by
runtime role:

- `bootstrap/`: startup config bootstrap, onboarding, data-root migration, and shared path helpers
- `lifecycle/`: compatibility imports for ingress, brain improvement, and runtime sidecars
- `diagnostics/`: debug registry and owner-status reporting

The former `integration/` bucket is dissolved: skill diagnostics belongs to
`modules/skill`, and vector synchronization belongs to `modules/storage`.

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
- facade packages that simply mirror `modules/` without adding runtime
  composition or integration ownership
