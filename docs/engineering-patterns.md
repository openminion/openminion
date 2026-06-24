# OpenMinion Engineering Patterns

Status: active
Last updated: 2026-06-20

Purpose: give public contributors one package-local summary of the engineering
patterns that shape `openminion` changes.

## Core rule

Prefer single-owner, explicitly named, typed surfaces over repeated literals,
ad hoc wrappers, or hidden cross-module coupling.

## The main owner split

Use this source-tree ladder when deciding where code belongs:

1. `api/` owns API runtime composition and request dispatch.
2. `base/` owns foundational contracts and shared primitives.
3. `cli/` owns command-line entrypoints and interactive UX.
4. `modules/` owns feature and subsystem packages.
5. `services/` owns cross-owner runtime orchestration.
6. `tools/` owns the tool runtime host and package-owned tool families.

## Shared-owner rules

1. Shared constants should live in their canonical owner rather than being
   repeated inline.
2. Operator-tunable values should live in config owners rather than being
   hardcoded at call sites.
3. Compatibility wrappers should stay thin and temporary.
4. Public roots should stay small and intentional; deep imports are not a
   blanket public promise.

## Runtime-boundary rules

1. Keep runtime behavior explicit and typed.
2. Prefer registries and documented seams over implicit discovery.
3. Avoid local semantic guesswork when the contract should stay structural or
   LLM-owned.
4. Keep feature owners in their subsystem rather than growing new root-level
   generic helpers.

## Cleanup and refactor rules

1. Preserve ownership clarity over blind line-count reduction.
2. Characterize behavior before collapsing non-trivial structure.
3. Prefer focused, reviewable refactors over broad mixed-purpose rewrites.
4. Keep temporary sweep artifacts out of the repo root; use the workspace temp
   area instead.

## Use with

Read this doc together with:

1. [`code-quality-enforcement.md`](code-quality-enforcement.md)
2. [`getting-started.md`](getting-started.md)
3. [`source-tree-owner-map.md`](source-tree-owner-map.md)

These package-local copies are the public-facing contributor summaries. The
broader workspace docs may go deeper, but external contributors should not need
them to understand the package contract.
