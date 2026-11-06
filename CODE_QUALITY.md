# OpenMinion Code Quality and Hygiene

This is the public contributor version of the project's code-quality rules.
It applies to maintainers, external contributors and coding agents, to new
changes and to reviews of code already merged.

The short version:

1. keep ownership clear,
2. keep code explicit,
3. keep structure predictable,
4. keep comments minimal,
5. and prove the change with validation.

## 1. Prefer one truthful owner

Do not scatter shared behavior across random files.

Use the nearest clear owner:

1. tunables in `config.py`
2. shared fixed values in `constants.py`
3. shared path/root rules in path-owner helpers
4. shared cross-cutting helpers in their canonical owner module

Avoid:

1. duplicate helpers
2. repeated magic literals
3. ad hoc wrappers
4. "just for this file" copies of shared logic

For each new layer, fallback or retry, identify the caller or failure it serves.
Prefer the existing owner when it can satisfy that case directly. Keep validation
where external data enters and preserve permission, compatibility, replay and
cleanup guarantees. If a second check serves the same contract, show what
can change between the two checks. Apply this review to the final diff before
merging, including maintainer PRs.

### Minimum usable code

Trace the actual callers, supported inputs and failure paths before choosing
the implementation. First ask whether the code is needed, then check the
existing owner, standard library, platform and existing dependencies. Prefer
a direct call or one small helper when it meets the same contract. Add a new
layer only for a current requirement, not an imagined future consumer.

Reject unused modes and extension points, pass-through wrappers, duplicate
validation, silent defaults that hide invalid state, blanket exception handling
and retries that hide failures. A retry needs a defined transient failure,
a bound and an idempotency or recovery contract. An internal typed path should
rely on its owner's contract unless state can change between checks.

Necessary defenses remain required: validate untrusted input and preserve
permissions, data integrity, compatibility, persistence, replay, resource
cleanup and observable failures. Fewer lines are not an improvement if they
remove one of those guarantees or make the flow harder to understand.

## 2. Code should explain itself

Write code so names, types, and structure do most of the explaining.

Rules:

1. every source file should start with a short factual top-of-file explainer
2. inside the file, commentary should be minimal
3. prefer no comment over obvious restatement
4. keep only short notes for real invariants, boundaries, policy, or compatibility

Avoid:

1. long narrative docstrings
2. section-banner comments
3. helper-history prose
4. comments that just paraphrase the code
5. the same explanation repeated in a function docstring, inline comment and test

Before submitting a PR, read every added or changed comment and docstring in
context. If removing it loses no information beyond the names, types and code,
remove it. Simplify unclear code before adding a walkthrough. Keep one concise
explanation beside the owner when a constraint is genuinely non-obvious.

Apply this review to tests and scripts too: keep the regression condition or surprising
oracle, but remove narration of setup, calls and assertions that already read
clearly. Preserve public API/tool descriptions consumed by users or models,
license notices, required directives, and useful safety or compatibility notes.
Those are contracts, not comment-count targets.

Reviewers should point to the exact redundant text and the code it repeats.
Passing lint or CI does not replace this readability review. For debt already
merged, check current `dev` and existing reports before opening one bounded
cleanup issue; keep unrelated cleanup out of a feature PR.

## 3. Keep runtime behavior structural, not speculative

Runtime code should enforce structure, policy, and safety. It should not guess meaning that belongs to model prompts or typed contracts.

Avoid:

1. keyword heuristics deciding intent
2. phrase matching as hidden routing logic
3. local semantic scoring when the owner should be explicit data or model output
4. free-form fallback interpretation that silently changes behavior

Prefer:

1. typed fields
2. explicit contracts
3. canonical registries
4. clear owner boundaries

## 4. Keep names and layout honest

Names should match what the code actually owns.

Rules:

1. remove stale names instead of letting them linger
2. avoid legacy alias surfaces unless they are temporary and intentional
3. do not create generic junk-drawer files like `utils.py`
4. keep files in the package area that truthfully owns them

If a module only exists for compatibility, make that explicit and plan its removal.

## 5. Keep generated artifacts out of the source tree

Generated, test, and runtime artifacts should go under the canonical OpenMinion runtime roots, not random repo-root folders.

Do not introduce:

1. stray repo-root artifact directories
2. hidden local output conventions
3. one-off file roots that bypass the shared path owners

## 6. Keep changes focused

Make small, reviewable changes.

Good practice:

1. one clear purpose per PR
2. update tests near the change
3. avoid unrelated refactors in the same patch
4. record any discovered adjacent cleanup debt instead of sneaking in a broad rewrite

## 7. Validate before calling work done

Before closing work, run the project gates from `openminion/`:

```bash
.venv/bin/python3.11 -m ruff check .
make lint
```

If your change touches behavior, also run the smallest focused tests that actually prove the change.

The test suite has its own ceiling: `scripts/validate/test_skip_budget.py` counts
`skip`/`skipif`/`xfail` markers per file and fails when a file gains one or when a
baselined count drops without `--emit-baseline`. It reads markers written on a test
or class, in a module-level `pytestmark` list, and inside
`pytest.param(..., marks=...)`.

A marker counts as unconditional when it statically applies to every run: `skip`, a
bare `pytest.mark.<kind>`, a marker with no conditions at all (`skipif()`), and a
`skipif`/`xfail` where any condition is a truthy literal. `condition=` overrides
positional arguments; otherwise every positional condition is considered and one
applying is enough. Boolean and numeric literals count directly; a string condition
counts only when it parses to a single literal, because pytest evaluates condition
strings as expressions.

The budget records which markers apply, not whether a case executes. Names, calls,
operator forms and non-literal strings are never evaluated, so they stay conditional
in the count whatever they evaluate to. An applied `xfail` may still run the test
(`run=False` prevents execution once the condition applies), and an indirect form
(a shared decorator, a `marks=` list assembled at runtime) is invisible to a static
scan. A file that cannot be parsed is reported as a scan error instead of
being counted as marker-free. Each baseline row carries an authored reason; `--emit-baseline` lowers
existing rows only and refuses new ones, so adding a marker means writing down why it
exists and defending it in review.

Committed numeric debt baselines are ceilings, not quality scores. Their
normal checks are read-only. When an existing owner improves, use that
validator's explicit `--emit-baseline` command to record only the lower value;
normal update commands reject increases, mixed increases, and new debt owners.

Use `make ci-check` for the provider-free pull-request sequence. It runs
workflow syntax, formatting, lint, the fast critical-path tests, the full
provider-free suite, and the bounded provider-free E2E slice. Keep live
providers, external services, Postgres, package integrations, benchmarks, and
slow suites in their existing integration or release lanes.

Public validator catalog:

1. `docs/testing-and-validation.md`

## 8. For broad cleanup work, start from the live file list

If you are claiming a family-wide or repo-wide cleanup, do not work from memory or a hand-picked subset.

Start from the live tree:

From the package root:

```bash
rg --files src/openminion -g '*.py' | sort
```

Then:

1. freeze the file list you used
2. sweep against that exact list
3. rerun the same sweep after edits
4. keep temporary ledgers and scan outputs in a temporary directory outside
   the tracked package tree

For file-by-file cleanup claims, also:

1. keep a per-file ledger
2. use explicit dispositions:
   1. `trim`
   2. `keep`
   3. `defer-owned:<tracker>`
   4. `defer-later:<reason>`
3. keep the remaining-file count explicit until it reaches zero

Keep source, tests and scripts inventories separate when their proof differs.
For each file, record the exact revision, callers checked, finding or keep
rationale, disposition and validation. Work in bounded tranches and update
the ledger and remaining count after each one. Refresh the inventory and
affected validation if the code changes during review.

For source simplification, record before/after LOC; revert a claimed reduction
that is LOC-neutral or harder to read. Preserve test assertions, negative
controls and coverage intent; do not add skips, xfails, waits or retries to
make a failing check pass. Record unchanged failures as baseline evidence.
Preserve scripts' exit codes, machine-readable output and operator clarity.
Deferred work must name its owner or reason and remain visible; deferral is
not completed cleanup.

## 9. Reduce complexity truthfully

OpenMinion now treats complexity, readability, and LOC reduction as first-class
cleanup work, not as an ad hoc side effect of random refactors.

Preferred mechanisms:

1. per-file, per-class, and per-method precision review
2. helper centralization by domain owner
3. plugin-family runtime simplification where scaffolding repeats
4. regrowth rebaseline lanes for files that have grown materially
5. feasibility or decision lanes when the target has product-scope tradeoffs

Do not:

1. cut lines blindly,
2. move logic into harder-to-follow indirection just to shrink a file,
3. claim a giant parent cleanup wave is finished while it still quietly owns a
   large unresolved backlog.

If a large cleanup lane becomes too broad, the honest pattern is:

1. land the bounded trims that are safe now,
2. route the remaining backlog to explicit child or leaf owners,
3. close the parent only when its remaining-file count is zero.

## 10. When in doubt, choose clarity over cleverness

The project prefers:

1. explicit owners over convenience
2. small truthful surfaces over broad magical ones
3. maintainable structure over short-term shortcuts

If a change makes the code harder to place, reason about, validate, or delete later, it is probably the wrong shape.

## 11. Review evidence and merged code

Review the complete final diff and every new delta, including rebases and
maintainer changes. Record the exact head, checked callers and supported
failure paths, findings, disposition and validation in the PR review or linked
ledger. Ask for the smaller change in the existing owner when it satisfies the
same contract. Block approval and merge while a substantiated finding remains;
green CI is not a substitute for this review. Recheck the live target, head,
integration and required checks before merging.

Apply the same rules to merged code, but first verify that the candidate still
exists on current `dev`. Reuse existing findings and active owners, record
resolved or obsolete candidates, and open only bounded follow-up work backed
by current caller or failure evidence. Do not reopen obsolete PRs, repeat
closed audits, duplicate issues or take over an active contributor's branch.
An old PR review is not proof that today's surviving code was checked.
