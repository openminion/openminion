# OpenMinion Package Docs

Status: public preview

This directory is the package-local documentation root for standalone
`openminion`. The broader website at <https://www.openminion.com/docs/> is the
friendlier operator and contributor manual; this folder stays close to the
package source and release surface.

## Start Here

| If you want to... | Read |
| --- | --- |
| Install the package from source and run local checks | [`getting-started.md`](getting-started.md) |
| Understand supported CLI, Python, API, gateway, and runtime surfaces | [`runtime-surfaces.md`](runtime-surfaces.md) |
| Operate local diagnostics, status, evidence, and runtime tools | [`system-operations.md`](system-operations.md) |
| Validate a source checkout or release candidate | [`testing-and-validation.md`](testing-and-validation.md) |
| See which claims are safe to make publicly | [`standalone-claim-alignment.md`](standalone-claim-alignment.md) and [`certification-readiness-matrix.md`](certification-readiness-matrix.md) |
| Find where code belongs before contributing | [`source-tree-owner-map.md`](source-tree-owner-map.md) |

## Runtime And Operator Topics

- [`terminal-surfaces.md`](terminal-surfaces.md): canonical terminal product,
  retired dashboard areas, and resource-command boundaries.
- [`memory-namespace-queries.md`](memory-namespace-queries.md): typed memory
  list/search filters across `memctl` and the local HTTP API.
- [`memory-review-workflow.md`](memory-review-workflow.md): digest-bound
  review-before-apply, SQLite rollback, and audit evidence for memory changes.
- [`graph-viewer.md`](graph-viewer.md): second-brain memory and third-brain
  provider graph inspection through GraphFakos.
- [`provider-capabilities.md`](provider-capabilities.md): explicit provider
  capability facts, request requirements, and deterministic pre-call routing.
- [`long-horizon-project-worker.md`](long-horizon-project-worker.md):
  checkpointed, operator-visible project-worker primitives and claim
  boundaries.

## Contributor And Quality Topics

- [`engineering-patterns.md`](engineering-patterns.md): package-local owner
  boundaries and engineering conventions.
- [`code-quality-enforcement.md`](code-quality-enforcement.md): public quality
  gates and validation expectations.
- [`pre-authoring-code-simplicity-and-readability-guideline.md`](pre-authoring-code-simplicity-and-readability-guideline.md):
  how to keep new code readable before cleanup is needed.

## Package Boundaries

`README.md`, `API_COMPATIBILITY.md`, `RELEASING.md`, and this docs directory are
public package guidance. `examples/` is the runnable teaching surface.

`tests/` and `scripts/` are proof, validation, and operator tooling. They are
important for contributors, but they are not the supported import API.

## Public Package Stance

The `v0.0.1` public preview contract is a local-first agent runtime with:

1. the canonical interactive CLI and one-shot `run` path,
2. a Python API rooted at `openminion` and `openminion.api`,
3. package-owned tool registration/decorator support,
4. package-owned config and portability helpers,
5. explicit runtime entrypoints for agent turns, API runtime composition, and
   local operator workflows,
6. early project-worker primitives for checkpointed, operator-visible
   long-horizon objectives with explicit proof and claim boundaries.
