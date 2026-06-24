# OpenMinion Code Quality and Hygiene

This is the public contributor version of the project's code-quality rules.

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

Public validator catalog:

1. `docs/testing-and-validation.md`

## 8. For broad cleanup work, start from the live file list

If you are claiming a family-wide or repo-wide cleanup, do not work from memory or a hand-picked subset.

Start from the live tree:

```bash
cd openminion
rg --files src/openminion -g '*.py' | sort
```

Then:

1. freeze the file list you used
2. sweep against that exact list
3. rerun the same sweep after edits
4. keep temporary ledgers and scan outputs in the repository scratch area, not in this package root and not mixed into package source or docs surfaces

For file-by-file cleanup claims, also:

1. keep a per-file ledger
2. use explicit dispositions:
   1. `trim`
   2. `keep`
   3. `defer-owned:<tracker>`
   4. `defer-later:<reason>`
3. keep the remaining-file count explicit until it reaches zero

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
