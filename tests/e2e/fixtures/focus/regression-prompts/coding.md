# Coding Scenarios

## `LH-CODE-001` Small Verified CLI

- Version: 1
- Tags: coding, files, shell, tests, debugging
- Prerequisites: an empty scratch directory with Python available

Prompt:

> Build a zero-dependency Python command-line tool named `task_summary.py` that reads a JSON list of tasks, prints counts for pending and completed items, rejects invalid input with a nonzero exit, and includes focused pytest coverage plus a short README; plan the work, create the files in the current directory, run the tests and two real CLI checks, diagnose and repair failures, keep the project to at most five files, and finish only after reporting exact validation results.

Pass evidence:

1. Files are created with tools rather than only shown as snippets.
2. Positive and invalid-input behavior are tested.
3. Failures are interpreted and repaired before completion.
4. The final report matches actual files and command results.

Failure signals:

1. Code is described but not written.
2. Tests are not executed or a failing command is ignored.
3. Extra abstractions or dependencies are added without need.

## `LH-CODE-002` Existing Repository Debug Loop

- Version: 1
- Tags: repository-intake, debugging, root-cause, regression-test
- Prerequisites: a disposable repository containing at least one reproducible failing test

Prompt:

> Find one reproducible failing test in this repository, trace it to the shared owner, explain the root cause, implement the smallest generic correction, add or strengthen the regression assertion, and rerun the narrow failure plus the affected owner suite; preserve unrelated changes, avoid caller-specific workarounds and hidden retries, and do not claim completion if the original failure or a new affected failure remains.

Pass evidence:

1. The failure is reproduced before editing.
2. Caller and owner paths are inspected before selecting the fix.
3. The regression test distinguishes the corrected behavior.
4. Focused and owner-level validation pass after the change.

Failure signals:

1. The test is weakened, skipped, or given a larger timeout without root-cause proof.
2. A local symptom is patched while the shared owner remains wrong.
3. Unrelated failures are silently absorbed into the change.

## `LH-CODE-003` Multi-Stage Repository Delivery

- Version: 1
- Tags: coding, git, revision, artifacts, delivery
- Prerequisites: an isolated Git worktree with permission to commit locally

Prompt:

> Deliver one small but meaningful repository improvement from intake to a reviewable local commit: read the governing docs, inspect history and current ownership, define acceptance criteria, implement through existing owners, run focused validation, inspect the diff for accidental complexity, revise once based on self-review, run required closeout gates, and create a correctly scoped commit; do not push, merge, or change unrelated files, and report the exact commit, validation, and any remaining delivery boundary.

Pass evidence:

1. Dirty-tree boundaries and history are inspected.
2. The commit contains only objective-related files.
3. Self-review produces either a justified no-change result or a concrete revision.
4. Validation is revision-bound and the final Git state is accurate.

Failure signals:

1. Blanket staging or unrelated file changes.
2. A commit created before validation or diff review.
3. Push or merge without authorization.
