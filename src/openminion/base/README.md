# Base

`openminion.base` is the shared foundation layer for OpenMinion.

It contains two kinds of owners:

1. shared primitive files used across top-level areas
2. a small admitted set of foundational subpackages that other areas depend on

## What belongs here

Primitive root files:
- `constants.py` — repo-global constants and env names
- `debug.py` — shared debug payload helpers
- `generated_paths.py` — generated/runtime artifact path helpers
- `logging.py` — process-global logging control-plane owner
- `protocol.py` — shared protocol frames and protocol helpers
- `redaction.py` — shared redaction helpers
- `time.py` — shared UTC timestamp helpers
- `types.py` — repo-wide message/response primitives
- `user_io.py` — shared user-IO protocol surface

Admitted foundational subpackages:
- `config/` — foundational config schema, parser, manager, and runtime policy resolution
- `channel/` — shared channel contracts and default registry
- `runtime/` — owner-neutral runtime primitives and sandbox contracts
- `errors/` — shared error envelopes and adapters

`config/` may own provider-neutral serialized records, parsing, and layer
precedence. Provider support, degradation behavior, mode semantics, credential
policy, and other feature interpretation stay with their module or service
owners. `runtime/` may own typed execution primitives and sandbox constraints;
orchestration and feature lifecycle behavior stay above Base.

## What does not belong here

Do not put these in `base/`:
- feature subsystems owned by a single top-level area
- runtime wiring and lifecycle glue that belongs in `services/`
- module-specific execution engines or storage that belong in `modules/`
- convenience shared owners promoted only because a literal string repeats

## Decision rule

Use this owner ladder in order:
1. local package owner first
2. area-root owner second
3. `openminion.base` only for repo-global primitives or admitted foundational subsystems

## Executable charter

`scripts/validate/base_charter.py` enforces this owner decision with:

- zero imports from Base into `api`, `cli`, `modules`, `services`, or `tools`, including lazy and type-only imports
- an exact reviewed Python-file inventory, so every new Base owner requires an owner-ladder review
- downward-only per-file budgets for LOC, callable count, maximum callable LOC, and maximum parameter count
- a 100-line callable ceiling, a 500-line default file ceiling, and a 9,500-line Base ceiling

`config/mcp.py` is the only approved large-file exception at 719 lines because
it is a declarative catalog under the active MCPH owner. Its budget cannot grow.
The exact budgets live in
`scripts/baselines/base_foundation_ratchet.tsv`; reductions must lower the
matching baseline row rather than retain unused allowance.

## Admitted-subsystems rationale

- `config/` stays because config parsing, config IO, and runtime policy resolution are foundational to every top-level area and CSC already stabilized this shape.
- `channel/` stays because channel contracts and the default registry are shared ingress primitives, not a feature subsystem.
- `runtime/` stays because it owns owner-neutral runtime interfaces, sandbox specs, and runners consumed across owners; cross-owner orchestration lives in `openminion.services.runtime`, and subsystem-internal execution helpers live in `openminion.modules.<X>.runtime`.
- `errors/` stays as a small support package because the error envelope/adapter surface is shared and cohesive, even though it is package-shaped instead of a single file.
