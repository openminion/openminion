# Contributing to OpenMinion

Thanks for contributing.

## Before coding

Read these docs before coding:

1. [Engineering Patterns](docs/engineering-patterns.md)
2. [Code Quality Enforcement](docs/code-quality-enforcement.md)
3. [Pre-Authoring Code Simplicity and Readability](docs/pre-authoring-code-simplicity-and-readability-guideline.md)
4. [Getting Started](docs/getting-started.md)
5. [OpenMinion Code Quality and Hygiene](./CODE_QUALITY.md)
6. [Testing and Validation](docs/testing-and-validation.md)

Treat items 1 and 2 as a pair:

1. env centralization,
2. `config.py` vs `constants.py` vs `paths.py`,
3. shared-owner and compatibility-wrapper rules,
4. explicit registration/routing patterns,
5. refactor validation discipline,
6. active CI gates, validator scripts, and cleanup categories.

Treat item 3 as the pre-write simplicity companion:

1. write direct code first,
2. run the minimum useful code ladder before adding custom structure,
3. avoid wrappers that add no policy or validation,
4. prefer concrete names and real owners,
5. avoid speculative event/callback abstraction when a direct call is clearer,
6. preserve validation, security, policy, telemetry/audit/replay seams, and
   public behavior while simplifying,
7. optimize for human readability before relying on cleanup later.

Use item 5 as the short contributor rulebook when you need a tighter package
boundary summary than the broader docs.

## Quick start

1. Fork and create a branch.
2. Run `make dev-install` and `make hooks-install` once in your clone.
3. Make focused changes.
4. Add or update tests.
5. Open a PR with a clear summary.

## Development basics

### From a report to a fix

Reporting a bug does not require submitting a PR or having paid provider
credentials. Maintainers remain responsible for triage and follow-through.

1. **Validate first.** Check the supported behavior, current source and existing
   fixes. Record a reproduction or the specific evidence still missing. Separate
   confirmed defects, enhancements and provider-specific behavior; a proposed
   workaround is not proof that a default or contract should change.
2. **Invite a focused contribution.** Ask whether the reporter wants to submit a
   fix, describe the smallest accepted scope and regression checks, and agree on
   an ETA. For design-sensitive changes, review a concrete proposal first. A
   report without a PR remains valid.
3. **Give contributors room.** Honor an agreed ETA. If there is no response,
   follow up after seven calendar days; if an ETA slips, ask for an update.
   Allow three calendar days after either follow-up before taking over. State
   those dates on the issue.
   These are coordination windows, not a guaranteed fix or release date.
4. **Take ownership when needed.** If the contributor declines, cannot implement
   the fix or cannot obtain required evidence, a maintainer owns that next step
   immediately. After the follow-up window expires, record the handoff before
   starting overlapping work. Urgent correctness or security work may proceed
   sooner with an explanation; security details follow `SECURITY.md`.
5. **Make the handoff visible.** Name the maintainer, next action and next update
   date; assign the issue when possible, remove obsolete contributor-wait labels,
   and link the focused PR as soon as it exists. Preserve reporter credit and
   useful contributor work. Ownership of an investigation does not establish a
   bug or justify an unmeasured value change. If blocked, record the missing
   prerequisite and a revisit date rather than repeatedly asking an unavailable
   reporter. Do not close a confirmed bug merely because its reporter cannot fix
   it.
6. **Review and verify.** Apply the same engineering, regression and CI gates to
   maintainer and contributor PRs. Reconcile with current `dev`, review the final
   diff and require successful checks on that head. Keep fixed issues open with
   `status:awaiting-release`, non-closing PR references and an installed published
   package verification checklist. Close only after the checklist passes on a
   release containing the fix. Invalid, duplicate or out-of-scope reports may be
   closed with an evidence-backed explanation.

When checking reports, inspect unanswered maintainer requests as well as new
issues and PRs. Continue agreed contributor work, take over explicit inability
or expired handoffs, and avoid duplicate reminders when nothing has changed.

### Implementation and review

Every contributor and coding agent, including maintainers and contributors
using code-generation tools, must follow [Code Quality and Hygiene](CODE_QUALITY.md). Before requesting
review, inspect the final diff for duplicate helpers, unnecessary abstractions,
and comments or docstrings that repeat the code. Names, types and direct control
flow should carry the explanation; retain only necessary context. See the
[comment review rule](CODE_QUALITY.md#2-code-should-explain-itself), including its
public-contract and test exceptions. Green CI alone is not readability approval.
Review each new delta as well as the complete final diff, and record the exact
head and caller/failure evidence. Follow the
[merged-code and review evidence rules](CODE_QUALITY.md#11-review-evidence-and-merged-code)
and the [cleanup ledger requirements](CODE_QUALITY.md#8-for-broad-cleanup-work-start-from-the-live-file-list).
Do not approve or merge with an unresolved, substantiated quality finding.

1. Follow existing style and project conventions.
2. Keep PRs small and reviewable.
3. Include validation commands and results in the PR description.
4. Prefer a short GitHub-native PR title plus a flat bullet summary of what the
   commit set landed.
5. Keep PR descriptions easy to scan and easy to copy: short title, flat
   line-item bullets, and a plain `Validation` section with exact
   commands/results.
6. For broad cleanup/code-quality lanes, start from a fresh live file inventory instead of a hand-picked subset. Preferred command:
   ```bash
   rg --files src/openminion -g '*.py' | sort
   ```
7. Keep temporary broad-sweep artifacts in a temporary directory outside the
   tracked package tree.
8. Prefer task-scoped validation during slice work; reserve broad repo-wide suites like `make check` for integration closeout or when a release or integration surface explicitly requires them.
9. Do not include unrelated refactors in the same PR.
10. For controlplane storage work, preserve the two-database rule:
    cross-channel state lives in `cp.db`; channel-private transient state stays
    in the channel database.

For the provider-free pull-request sequence, run `make ci-check`. It composes
workflow syntax, formatting, lint, the fast critical-path tests, the full
provider-free suite, and the bounded provider-free E2E slice. `make test` and
`make check` remain broader local suites; live providers and external services
keep their existing opt-in gates.

Commit message guidance:

1. Use commit messages in the form `<type>(<scope>): <summary>` for normal work
   in this repo.
2. Approved current types are `feat`, `fix`, `docs`, `refactor`, `test`,
   `chore`, `style`, and `build`.
3. In `openminion`, include a scope by default. Good scopes name the owner
   surface directly, for example `agent`, `api`, `cli`, `e2e`, `gateway`,
   `runtime`, `telemetry`, `tool`, `tools`, or `tui`.
4. Keep the summary specific to the landed change and avoid vague messages like
   `update`.
5. Prefer the most specific truthful type; do not use `chore` when `docs`,
   `test`, `refactor`, or `build` is more accurate.
6. Do not use local shorthand or planning labels as normal commit types.

Preferred PR shape:

`Add workspace persistence mode`

- add ...
- align ...
- polish ...

Validation
- `<command>`
- `<command>`

## Legal basics (plain English)

To keep contribution friction low:

1. You keep ownership of your contributions.
2. By submitting a contribution, you license it under the project license (Apache License 2.0).
3. Apache-2.0 includes a patent license for your contribution, with the standard patent-termination condition in the license text.
4. Only submit code/content you have the right to contribute.
5. Do not add third-party code/assets unless their license is compatible and clearly documented.
6. Project names/logos are not granted for endorsement use.
7. OpenMinion is provided on an "as is" basis under the project license; there are no guarantees about performance, reliability, availability, cost outcomes, or malfunction-related consequences.
8. If you configure third-party providers or paid infrastructure while developing or testing, you are responsible for any resulting charges.
9. See [LICENSE](./LICENSE) for the full legal terms, disclaimers, and limitations of liability.

## Security

If you find a security issue, do not open a public issue with exploit details. Use the project security reporting process.

## Code of conduct

By participating, you agree to follow [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md).
