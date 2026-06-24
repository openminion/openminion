# OpenMinion Standalone Claim Alignment

Status: active
Last updated: 2026-06-23

Purpose: keep public `openminion` package claims aligned with the surfaces that
ship today.

## Public claim alignment

| Public claim | Current shipped surface | Proof location | Status |
| --- | --- | --- | --- |
| local-first Python agent runtime | package metadata, CLI entrypoint, runtime package layout | `pyproject.toml`, `README.md`, package-local release proof | aligned |
| stable package-level Python API | top-level `openminion` exports `APIRuntime`, `Agent`, `AgentRunResult`, `Handoff`, `OpenMinionConfig`, `MemoryBundle`, `tool`, `subagent`, `__version__` | `src/openminion/__init__.py`, `API_COMPATIBILITY.md`, root import smoke | aligned |
| public API runtime surface | `openminion.api` re-exports `APIRuntime`, `Agent`, `Handoff`, `dispatch_request`, and related helpers | `src/openminion/api/__init__.py`, `src/openminion/api/README.md`, targeted package regression tests | aligned |
| interactive CLI surface | `openminion` console script and module entrypoint | `pyproject.toml`, `README.md`, CLI smoke gate | aligned |
| operator subcommands and companion CLIs | `openminiond`, `artifactctl`, `memctl`, `brainctl`, `policyctl`, and related package-owned entrypoints | `pyproject.toml`, package-local `make lint` plus public-surface validators | aligned |
| examples as runnable teaching surfaces | top-level `examples/` files and `examples/modules/sample` | `examples/`, `docs/runtime-surfaces.md`, `python -m compileall examples` | aligned |

## Current package line

The current public package line is `0.0.1`. This document records alignment of
the public claims to the currently provable package surface and keeps the
package contract honest while the project remains in alpha.

## Claims intentionally not made

The package should not publicly claim that all deep imports under these areas
are stable:

1. `openminion.modules.*`
2. `openminion.services.*`
3. `openminion.tools.*`
4. repo-local `tests/` and `scripts/`

Why: these trees are meaningful owners, but they are not blanket stable API
promises just because they are importable inside the repo.

## Shorthand rule

When a public doc, GitHub blurb, or release note mentions `openminion`, keep
the claim inside one of these buckets:

1. documented root/library exports,
2. documented CLI/operator entrypoints,
3. documented API runtime surfaces,
4. documented runnable examples,
5. documented package-local release and compatibility docs.
