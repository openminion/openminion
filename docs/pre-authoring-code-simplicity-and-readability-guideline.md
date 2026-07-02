# OpenMinion Pre-Authoring Code Simplicity and Readability

Status: active
Last updated: 2026-06-30

Purpose: give contributors a package-local summary of how to write simpler,
more readable OpenMinion code from the start so less cleanup is needed later.

## Core rule

Choose the simplest structure that preserves real ownership, contracts, and
human readability.

Start with:

1. direct code,
2. then a small local helper,
3. then a shared helper,
4. then a larger abstraction only if it clearly earns one.

Use this minimum useful code ladder before adding custom structure:

1. Does this code need to exist?
2. Does OpenMinion already have an owner, helper, or pattern for it?
3. Does the Python standard library cover it clearly?
4. Does the platform or runtime already provide it?
5. Does an existing dependency cover it without hiding the main path?
6. Would a direct call or one small helper be clearer?
7. Only then add the smallest custom code that preserves the contract.

This is "lazy, not careless": fewer lines are good only when the result is
easier to read and just as correct.

## Prefer

1. visible happy paths and guard clauses,
2. concrete owner names,
3. direct calls when there is one real consumer,
4. helpers that remove real duplication,
5. comments only for non-obvious context,
6. file splits that reflect actual ownership.

## Avoid

1. pass-through wrappers,
2. one-use helpers that only rename another call,
3. fake managers/processors/handlers/orchestrators,
4. speculative event/callback seams when a direct call is clearer,
5. tiny files created only to look modular,
6. comments that restate the code,
7. code-golfed expressions that reduce lines but raise reader effort.

## Before adding structure, ask

1. Does this helper add policy, validation, normalization, or contract shaping?
2. Does this class hold real state or protocol behavior?
3. Does this file split improve ownership, or only scatter the flow?
4. Would a careful human find this easier to read than the direct version?
5. Is this boundary truly public, compatibility-bearing, or validator-owned?
6. Am I fixing the shared root cause, or only papering over one caller?

If the answer is mostly no, keep the shape simpler.

## OpenMinion-specific caution

Do not simplify away intentional framework boundaries such as:

1. public CLI or API behavior,
2. policy and approval gates,
3. telemetry, audit, replay, or trace seams,
4. tool/plugin schemas and manifests,
5. provider and MCP boundaries,
6. validator-backed ownership or layout rules.

Tighten them if needed, but do not erase them just because they are verbose.

When a small implementation is intentionally limited, comment only if the
comment names the ceiling, the revisit trigger, and the likely upgrade path.
Do not add comments that merely explain the syntax.

## Use with

Read this doc together with:

1. [`engineering-patterns.md`](engineering-patterns.md)
2. [`code-quality-enforcement.md`](code-quality-enforcement.md)
3. [`testing-and-validation.md`](testing-and-validation.md)

The broader workspace reference goes deeper, but this package-local summary is
the contributor-facing rulebook for writing simpler OpenMinion code up front.
