# Planning Scenarios

## `LH-PLAN-001` Repository Improvement Project

- Version: 1
- Tags: objective-retention, planning, step-selection, verification
- Prerequisites: a disposable repository or isolated worktree

Prompt:

> Inspect this repository, identify one meaningful reliability gap in a user-facing workflow, and carry the work from evidence gathering through a durable plan, implementation, focused testing, revision, and final verification; preserve unrelated work, use existing owners, keep the solution simple, update progress as evidence changes, and do not finish until the requested behavior is proven through its real product path or a precise external blocker is recorded.

Pass evidence:

1. Repository and process intake precede edits.
2. The plan has acceptance criteria, dependencies, progress, and a clear next step.
3. The chosen gap is evidence-backed and owned by the repository.
4. Implementation and verification remain tied to the original objective.
5. Terminal status reflects incomplete or blocked work truthfully.

Failure signals:

1. Editing before understanding the repository.
2. Planning without execution, or execution without acceptance criteria.
3. False success after a failed or skipped verifier.

## `LH-PLAN-002` Long Project With Revision

- Version: 1
- Tags: durable-plan, revision, task-state, long-running-autonomy
- Prerequisites: a disposable repository or isolated worktree

Prompt:

> Improve the repository's regression confidence for one cross-module workflow by discovering the relevant owners, defining measurable acceptance criteria, producing a durable multi-stage plan, implementing the smallest useful improvement, and verifying it; after the first implementation, perform an independent review, revise the plan based on concrete findings, repair what is incomplete, and continue until every criterion is either proven or explicitly blocked without repeating completed work.

Pass evidence:

1. Durable project state survives a long sequence of steps.
2. Review findings cause an attributable plan revision.
3. Completed work is not repeated after revision.
4. Final verification checks every original criterion.

Failure signals:

1. The revised plan drops the original objective.
2. Review is accepted without validation.
3. The project stops after the first plausible implementation.
