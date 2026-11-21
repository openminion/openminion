# Skill maintenance

Status: active
Last updated: 2026-11-21

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

## Imported invocation policies

Ingest and the skill harness warn whenever either exact field is present,
regardless of its value:

- `agents/openai.yaml`: `policy.allow_implicit_invocation` produces
  `parse.warning:unsupported_invocation_policy:openai.policy.allow_implicit_invocation`.
- `SKILL.md` front matter: `disable-model-invocation` produces
  `parse.warning:unsupported_invocation_policy:claude.disable-model-invocation`.

These policies are **not enforced**. After admission, OpenMinion may use the
skill automatically. Do not admit it if manual-only behavior is required.
Native or portable structural success does not certify invocation compatibility.
Warnings do not grant permissions, admit a version, or change selection.
Existing unknown-key diagnostics remain visible.

The companion reader resolves the bundle root and skips symlinked `agents/` or `openai.yaml`,
non-regular files, and files larger than the existing 1,000,000-byte resource
limit. Skips produce `parse.warning:companion_metadata_skipped:<reason>` with
`symlink`, `non_regular`, or `file_size_limit`, plus the existing
unavailable warning. Unread content cannot produce invocation-policy diagnostics.
Checks cover static files, not adversarial concurrent filesystem replacement;
malformed YAML and read errors retain their existing error behavior.

Valid bounded companions retain their metadata and fingerprints. Stored packages
are not rewritten. Re-ingesting an unsafe companion may change the fingerprint
because skipped metadata is excluded. Revalidate or re-ingest existing bundles
to obtain these diagnostics; a successful import is not a manual-only guarantee.

For local sharing, see [export a stored skill bundle](guides/skill-bundle-export.md).
Export preserves retained content, not local admission or a secrecy guarantee.

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
