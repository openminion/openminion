# OpenMinion Source Tree Owner Map

Status: active
Last updated: 2026-06-18

Purpose: give contributors a package-local owner map for `src/openminion/`
without turning deep imports into blanket public API promises.

Unlike smaller sibling packages, OpenMinion keeps this owner map under
`docs/` rather than `src/openminion/README.md` because the root-layout
validator keeps `src/openminion/` limited to runtime entrypoint files and the
top-level owner directories listed below.

## Public boundary shorthand

Public package entrypoints live primarily at:

1. `openminion`
2. `openminion.api`
3. package-owned console scripts defined in `pyproject.toml`

Everything else in the source tree is an owner map first, not a blanket public
compatibility promise.

## Top-level owners

1. `api/` — API runtime composition, request dispatch, and HTTP-facing surface
2. `base/` — foundational contracts and shared primitives
3. `cli/` — command-line entrypoints and interactive UX surfaces
4. `modules/` — feature and subsystem owners
5. `services/` — cross-owner runtime orchestration and service seams
6. `tools/` — tool runtime host plus package-owned tool families

## Boundary rule

Use this owner ladder:

1. documented public package root first,
2. documented package-local facade next,
3. internal owner package last.

Deep imports can be useful inside the repo, but they should not be treated as a
public API promise unless a narrower compatibility doc says so explicitly.
