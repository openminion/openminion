# OpenMinion Package Docs

Status: alpha

This directory holds the public package documentation for standalone
`openminion`.

## Package-local references

- [`engineering-patterns.md`](engineering-patterns.md)
  records the package-local public summary of OpenMinion engineering
  conventions and owner boundaries.
- [`code-quality-enforcement.md`](code-quality-enforcement.md)
  records the package-local public summary of validation gates and
  contributor-quality expectations.
- [`pre-authoring-code-simplicity-and-readability-guideline.md`](pre-authoring-code-simplicity-and-readability-guideline.md)
  records the package-local contributor guide for writing simpler, more
  human-readable code up front rather than depending on cleanup later.
- [`getting-started.md`](getting-started.md)
  records the package-local bootstrap and execution summary for contributors and
  automation.
- [`standalone-claim-alignment.md`](standalone-claim-alignment.md)
  keeps public package claims aligned with the surfaces that ship today.
- [`certification-readiness-matrix.md`](certification-readiness-matrix.md)
  maps each public capability area to its current proof and remaining alpha
  gaps.
- [`runtime-surfaces.md`](runtime-surfaces.md) records the
  package-owned CLI, API, gateway, and Python-library surfaces and their
  intended use.
- [`terminal-surfaces.md`](terminal-surfaces.md) records the canonical terminal
  product, compatibility aliases, dashboard migration map, and retirement
  gates.
- [`testing-and-validation.md`](testing-and-validation.md)
  records the package-local smoke flow, validation gates, and public
  first-user checks.
- [`long-horizon-project-worker.md`](long-horizon-project-worker.md)
  records the alpha project-worker substrate, current proof shape, and the
  boundary between compressed pilot evidence and real elapsed multi-day claims.
- [`memory-namespace-queries.md`](memory-namespace-queries.md)
  documents typed memory list/search filters across `memctl` and the local HTTP
  API, including the operator-security and legacy-scope boundaries.
- [`provider-capabilities.md`](provider-capabilities.md)
  documents explicit provider capability facts, request requirements, and
  deterministic pre-call routing behavior.

## Package-local code/docs boundaries

1. `README.md` is the public package contract and install surface.
2. `API_COMPATIBILITY.md` records the supported public import roots,
   entrypoints, and compatibility posture.
3. The Source Tree Owner Map reference explains the source-tree owner map and
   public-vs-repo-local boundary.
4. `RELEASING.md` records the package-local release smoke flow and docs sync
   expectations.
5. `examples/` is the package-owned runnable teaching surface for starter
   snippets, agent/skill bundles, identity examples, and example module
   wiring.

## Repository-local but not package API

1. `tests/` is proof and regression coverage, not the public library API.
2. `scripts/` is developer/operator tooling, validators, and CI support rather
   than a supported import surface.
3. broader maintainer architecture and governance materials are not part of
   the package API surface.

## Public package stance

The current alpha contract is a local-first agent runtime with:

1. a default interactive CLI surface,
2. a Python API rooted at `openminion` and `openminion.api`,
3. package-owned tool registration/decorator support,
4. package-owned config and portability helpers,
5. explicit runtime entrypoints for agent turns, API runtime composition, and
   local operator workflows.
6. alpha project-worker primitives for checkpointed, operator-visible
   long-horizon objectives with explicit proof and claim boundaries.
