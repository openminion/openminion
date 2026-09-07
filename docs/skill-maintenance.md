# Skill maintenance

Status: active
Last updated: 2026-09-06

This page summarizes the current maintenance contract for the skill subsystem.
It is not a claim that every authored skill is useful or safe for every task.

## Current surface

OpenMinion supports:

- Markdown/front-matter skill ingestion
- immutable package versions and active-version selection
- local and remote provenance metadata
- explicit operator admission and rollback
- bounded `references/`, `assets/`, and `scripts/` resources
- workflow and tool-recipe records
- model-owned skill matching through typed candidates
- proposal, review, and learning records

Bundled skill scripts are stored as resources with `executable=false`; the
skill runtime does not execute them. A tool or workflow may execute only
through its normal runtime, exposure, policy, and approval boundaries.

## Maintainer checks

When the skill package changes:

1. Keep structural parsing, versioning, trust metadata, and admission in the
   runtime owner.
2. Keep semantic relevance and usefulness judgment model-owned.
3. Preserve explicit operator authority for admission, rollback, and catalog
   changes.
4. Add behavior tests at the owner boundary rather than duplicating checks in
   callers.
5. Run the focused skill tests, repository Ruff, and `make lint`.

Focused tests live under `tests/skill/`, `tests/tools/skill/`, and the
skill-related runtime slices in `tests/`.

## Design boundary

Do not add a second selector, local intent classifier, per-model branch, or
skill-specific tool dispatcher. Structural triggers may narrow complete typed
candidates, but the model chooses among them. Tool execution remains owned by
`openminion.modules.tool` and durable outcome learning remains owned by
`openminion.modules.memory`.

The current package contract is documented in
`src/openminion/modules/skill/README.md`.
