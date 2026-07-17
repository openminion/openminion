# OpenMinion Modules

## Purpose

`openminion.modules` owns feature subsystems: schemas, adapters, protocol
contracts, storage/runtime helpers, and any internal execution engines those
subsystems operate.

## Ownership split with `services/`

Three subsystems pair with a `services/` peer that handles runtime wiring for
that area:

- `brain/`
- `context/`
- `identity/`

When a paired subsystem changes shape, update the module owner and the matching
service owner together. Module packages own domain contracts, schemas,
adapters, and storage-facing engines; service packages own runtime composition,
policy wiring, lifecycle hooks, and cross-module orchestration. Do not move code
between the two layers unless the owning tracker names the paired owner,
validator effect, and behavior tests.

The following subsystems are standalone domain owners with no `services/` peer:

- `a2a/`
- `artifact/`
- `controlplane/`
- `llm/`
- `memory/`
- `policy/`
- `prompting/`
- `registry/`
- `retrieve/`
- `secret/`
- `session/`
- `skill/`
- `storage/`
- `task/`
- `telemetry/`
- `tool/`

`modules/` does not mirror `services/` one-to-one. Runtime glue such as
`services/runtime/` remains cross-owner orchestration, not a peer package for
every module with runtime helpers. Service paths for policy, tool selection,
channel authenticity, stats, and similar module-owned behavior are
compatibility imports or runtime wiring, not parallel feature owners.

## Root allowlist

Only shared module-layer helpers live at the `modules/` root:

- `base.py`: optional module base classes and descriptors
- `cli_common.py`: shared module CLI bootstrap/env helpers
- `config.py`: shared module config/home/data-root helpers
- `constants.py`: shared fixed module-layer semantics
- `providers.py`: fail-fast generic module/provider registry helpers
- `paths.py`: shared module path-layout constants

Feature logic does not belong at the root.

## Canonical subsystem template

A full-featured subsystem usually includes some subset of:

- `interfaces.py`
- `schemas.py` or `schemas/`
- `contracts.py` or `contracts/`
- `adapters/`
- `service.py` and/or `runtime/`
- `config.py`
- `constants.py`
- `errors.py`
- `README.md`

Not every subsystem needs every element. Small primitive subsystems may stay
flatter, and engine-owning subsystems may extend the template with `runtime/`,
`loop/`, `storage/`, or similar internal execution surfaces. Deviations are
acceptable when the subsystem README documents them.

## Module-local `runtime/` rule

Inside a subsystem, `runtime/` means internal execution-time engine or per-call
helpers that the module's public `contracts.py` / `interfaces.py` do not
expose. It is not a general "misc internals" folder.

If the contents are actually backend drivers, transport clients, or
bundle/asset machinery, prefer a more specific folder name:

- `backends/` or `drivers/` for concrete storage/persistence implementations
- `transport/` for HTTP/RPC client and protocol adapters
- `bundles/` for asset generation, parsers, renderers, and lockfiles
- another explicit owner name when the contents do not fit any of the above

`<module>/runtime/` is not the same role as `services/runtime/`
(cross-owner orchestration) or `base/runtime/` (owner-neutral primitives).

## Boundaries

Not in `modules/`:

- runtime wiring for paired subsystem areas (that belongs in `services/X/`)
- CLI or HTTP transport surfaces (that belongs in `cli/` and `api/`)
- cross-cutting primitives that belong in `base/`
- thin pass-through facades whose only purpose is to mirror a `services/`
  package; collapse or route those through the paired owner instead
