# OpenMinion Code Quality Enforcement

Status: active
Last updated: 2026-06-20

Purpose: summarize the public contributor view of OpenMinion's active quality
gates and validation posture.

## What contributors should expect

OpenMinion enforces code quality through four layers:

1. package-level conventions and owner boundaries,
2. pre-authoring simplicity rules that prevent avoidable wrapper and
   boilerplate drift before code lands,
3. automated validation scripts and lint gates,
4. focused cleanup/refactor discipline when a surface needs structural work.

## Required local validation

For normal contribution work, run:

```bash
cd openminion
make lint
```

For broader local proof, also run:

```bash
cd openminion
make check
```

Use narrower task-scoped pytest commands during iteration, then record the
commands you actually ran in the PR description.

## What the gates protect

The active checks are designed to catch drift in areas such as:

1. environment/config centralization,
2. import-boundary violations,
3. config-owner misuse,
4. logging and runtime control-plane drift,
5. duplicated helpers and misplaced shared constants,
6. root-layout and public-surface regressions.

## Public validation expectations

1. Keep changes focused and reviewable.
2. Include exact validation commands and results in the PR description.
3. Do not treat every importable path as stable public API; use
   `API_COMPATIBILITY.md` as the package promise.
4. Do not mix unrelated cleanup into a feature PR.

## When work is cleanup or refactor heavy

1. Start from a fresh live inventory instead of a hand-picked subset.
2. Keep sweep artifacts in the workspace temp area rather than the repo root.
3. Use focused regression proof before and after structural moves.

## See also

1. [`engineering-patterns.md`](engineering-patterns.md)
2. [`pre-authoring-code-simplicity-and-readability-guideline.md`](pre-authoring-code-simplicity-and-readability-guideline.md)
3. [`getting-started.md`](getting-started.md)
4. [`testing-and-validation.md`](testing-and-validation.md)

These package-local notes are the public-facing contributor summary. The
full maintainer inventory lives in the broader repo-maintenance documentation,
but contributors should be able to work from this package-local summary alone.
