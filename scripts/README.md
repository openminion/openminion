# Scripts

Last updated: 2026-06-22
Status: Active

Purpose: define the taxonomy for `openminion/scripts/` so validation entrypoints
live under one obvious owner and the top-level tree stays readable for public
contributors.

Public validator catalog:

1. the package-local testing and validation guide

## In scope

1. The `validate/`, `manual/`, `smoke/`, `ci/`, `common/`, and `baselines/`
   owners.
2. The naming rule for validation entrypoints.
3. The meaning of the focus-specific subvalidators.

## Out of scope

1. Validator implementation details.
2. Test runner ownership.
3. Docs-only generators or documentation-build helpers.

## Success criteria

1. `scripts/validate/` is the obvious home for repo guardrails.
2. Non-generic scripts have obvious category homes.
3. New work does not add project-specific shorthand names to the `scripts/`
   root namespace.
4. Validation entrypoints remain generic enough to protect durable repo
   contracts, not one temporary migration, one provider, or one isolated
   rollout.

## Validate contract

Generic repo guardrails live under `scripts/validate/`.

Use this shape:

1. `scripts/validate/<generic_repo_contract>.py`
2. `scripts/validate/focus/<focus_subcontract>.py` for subvalidators that feed a
   broader `focus_layout.py`-style entrypoint.

Do not keep repo validators at the `scripts/` root. Do not keep shorthand
project acronyms, temporary rollout names, smoke launchers, or docs-only
inventory generators in `scripts/validate/`.

Historical context belongs in docs or baseline comments when needed. Validator
docstrings and CLI output should describe the repo contract they enforce, not
the internal cleanup effort that introduced them.

If an external contributor asks "which validators are safe to treat as the
public repo guardrails?", point them to the validator catalog above rather than
to internal cleanup notes.

## Subfolder taxonomy

### `validate/`

Generic repo validators and lint gate entrypoints.

### `manual/`

Manual or domain-specific guards that are useful to keep in-repo but are not
part of `make lint`.

Examples:

1. narrow migration reintroduction guards,
2. pre-promotion validators for a single domain rollout,
3. runbook-owned surface checks.

### `smoke/`

Operational smoke-launch or measurement scripts.

These may exercise launch wiring or local runtime paths, but they are not
generic repo validators.

### `ci/`

CI plumbing helpers used by workflows, bundle generation, wheel building, or
migration check orchestration.

### `baselines/`

Static baseline artifacts consumed by validators.

### `common/`

Tiny internal helpers shared by repo-maintenance scripts.

Use this for terminal-output consistency or other script-local support code
that should not become part of the public validator surface.

## Maintenance Notes

1. If a script only writes documentation artifacts, keep it in the docs/tooling
   surface rather than the runtime validator tree.
2. If a script is valuable but not generic enough for `validate/`, move it
   under `manual/` or `smoke/` instead of forcing it into `make lint`.
3. If a proposed validator filename depends on a temporary project acronym or
   private shorthand, rename it before landing.
4. Manual-only guards should not keep a `validate_` prefix in the filename just
   because they perform checking. Put them under `manual/` with a direct,
   human-readable owner name instead.
5. Retired one-shot migration helpers should rely on git history instead of a
   live `_archive/` folder.
6. `scripts/` root stays limited to folder owners, docs, and package markers.
7. `scripts/validate/self_improvement_contract.py` and
   `scripts/validate/recovery_pipeline_contract.py` are the canonical
   contract-first names for the self-improvement metadata guard and typed
   recovery-pipeline guard.
8. `scripts/validate/tool_selection_scoring_contract.py` and
   `scripts/validate/runtime_step_ownership.py` are the canonical
   contract-first names for the two runtime anti-LLM guards.
9. `scripts/validate/focus_layout.py` is the lint entrypoint for the canonical
   interactive-surface layout check. Its widget-isolation guard keeps Textual
   Focus independent from dashboard body widgets.
10. `scripts/validate/runner_delegates.py` is a generic brain-runner contract
   guard: every generated `RUNNER_DELEGATES` key must have a static source/test
   consumer, and every `_runner_delegate("...")` call must target a defined
   key.
11. `scripts/validate/openminion_root_layout.py` is the package-root layout
    guard: root feature packages must not bypass the canonical `api/`, `base/`,
    `cli/`, `modules/`, `services/`, and `tools/` owner families.
12. `scripts/validate/no_source_e2e_artifact_refs.py` keeps generated E2E proof
    paths out of `src/openminion/`; source may expose artifact APIs, but it
    must not embed transient proof-output paths.
13. `manual/audit_characterization_snapshot_brittleness.py` is intentionally
    manual-only: it audits characterization-test brittleness for targeted review
    work, but it is not a generic repo gate and does not belong in
    `scripts/validate/`.
14. `scripts/validate/mypy_error_budget.py` is the canonical typecheck ratchet
    entrypoint; keep historical or temporary names out of the filename.
15. When a validator emits machine-readable JSON, keep the JSON payload on
    stdout and put human-readable headings, summaries, and findings on stderr
    so CLI users get readable output without breaking JSON consumers.
16. `scripts/validate/filename_underscore_hygiene.py` is the advisory
    naming-practice guard for Python files under `src/`, `scripts/`, and
    `examples/`; it tracks current multi-underscore filename debt with a frozen
    baseline and warns on new drift there. Even when drift is zero, the report
    still surfaces current `src/` multi-underscore filenames as context-
    dependent readability/refactor candidates so naming debt stays visible.
    Use `--show-src-detail` to print the full `src/` advisory list. `tests/`
    still appears in the report for visibility, but test-file multi-underscore
    names are informational and do not fail the naming lane. The default report
    shows the test-count summary only; use `--show-tests-detail` to print the
    full test-path list.
18. `scripts/validate/path_structure_hygiene.py` is the structural naming guard
    for `src/openminion/`; it blocks deprecated folder spellings such as
    `parsing/`, `focus_terminal/`, and `knowledge_graphs/`, rejects redundant
    suffixes such as `_runtime` and `_events`, and catches filenames that
    repeat the parent owner instead of letting the folder carry subsystem
    context.
19. `scripts/validate/helper_duplicates.py` is the canonical duplicate-helper
    guard; keep the filename aligned with the readability rule it enforces
    instead of preserving `*_helpers.py` wording in the validator surface.
