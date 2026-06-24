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

## Admitted-subsystems rationale

- `config/` stays because config parsing, config IO, and runtime policy resolution are foundational to every top-level area and CSC already stabilized this shape.
- `channel/` stays because channel contracts and the default registry are shared ingress primitives, not a feature subsystem.
- `runtime/` stays because it owns owner-neutral runtime interfaces, sandbox specs, and runners consumed across owners; cross-owner orchestration lives in `openminion.services.runtime`, and subsystem-internal execution helpers live in `openminion.modules.<X>.runtime`.
- `errors/` stays as a small support package because the error envelope/adapter surface is shared and cohesive, even though it is package-shaped instead of a single file.
