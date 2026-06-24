<!-- MFSS charter -->
# Tool Module

Owner: `openminion-tool`
Shape: `engine-owning`
Runtime peer: paired with `openminion.services.tool`

This module Owns the canonical tool runtime contracts, bootstrap, registry, family/runtime helpers, and tool-facing diagnostics. Primary contracts: `contracts/*`, `bootstrap/*`, `registry/*`, `runtime/*`. Typed tool manifests and schemas live in `contracts/*`; this package keeps a richer reference doc below the charter.

See `REFERENCE.md` for the full Tool Module System tutorial and reference doc.
For contributor authoring rules, family classification, typed execution-fact
guidance, and shared formatter / approval / provenance ownership, see
the package-local contributor and code-quality guidance.

Boundary note: reviewed runtime-hook bridges still exist for sidecar autostart
in `executor.py` and `cli/runtime.py`; these remain explicit validator-backed
exceptions rather than hidden ownership leaks. See
the package-local owner guidance for current boundaries.
