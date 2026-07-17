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

Eight service subpackages are standalone runtime concerns with no
`modules/` peer:

- `agent/`
- `bootstrap/`
- `cron/`
- `diagnostics/`
- `gateway/`
- `health/`
- `runtime/`
- `supervision/`

`services/` does not mirror `modules/` one-to-one. Module-only owners such as
`llm/`, `memory/`, `session/`, `storage/`, and `skill/` stay in the modules
layer. Policy, tool-selection, channel-policy, and stats behavior lives under
its canonical module owner. Remaining service paths for those areas are
compatibility imports or runtime wiring, not parallel feature owners.

### Compatibility-only packages

These packages accept no new behavior and exist only to preserve old import
paths during staged migrations:

- `channel/`
- `lifecycle/`
- `stats/`
- `tool/`

`security/` is transitional: its policy, tool-execution, blast-radius, and
validation paths are compatibility imports, while the service-owned validation
composition now lives under `diagnostics/security.py`.

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
