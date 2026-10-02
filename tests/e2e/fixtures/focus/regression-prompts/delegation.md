# Delegation Scenarios

## `LH-DEL-001` Parallel Repository Assessment

- Version: 1
- Tags: subagents, decomposition, bounded-handoff, synthesis
- Prerequisites: delegation support and a repository with tests

Prompt:

> Assess this repository's readiness for a long-running autonomous coding task by delegating bounded investigations of architecture ownership, test and verification coverage, and failure-recovery behavior, giving each investigator only the context and deliverable it needs; validate every returned claim against repository evidence, resolve disagreements or incomplete work, and synthesize one prioritized action plan without editing the repository.

Pass evidence:

1. Work is decomposed into non-overlapping delegated tasks.
2. Handoffs include scope, expected result, and stopping boundary.
3. Parent validation catches unsupported or incomplete child claims.
4. The final synthesis preserves provenance and dependencies.

Failure signals:

1. Delegates receive the entire unbounded objective.
2. Child summaries are copied into the final answer without validation.
3. The parent loses ownership of completion or marks partial work complete.

## `LH-DEL-002` Delegated Change And Independent Review

- Version: 1
- Tags: subagents, coding, dependency-handling, review, repair
- Prerequisites: delegation support and a disposable repository or worktree

Prompt:

> Select one small repository defect with an executable acceptance test, delegate source-owner investigation and test-design review as separate bounded tasks, implement the fix only after reconciling their evidence, then delegate an independent review of the exact resulting revision; validate the review, repair any demonstrated gap, rerun the verifier, and report child outcomes separately from the parent's final completion decision.

Pass evidence:

1. Delegated tasks have explicit dependencies and exact artifact or revision scope.
2. The implementation follows validated evidence rather than majority opinion.
3. Independent review is bound to the final change and checked by the parent.
4. Failed review causes repair and re-verification.

Failure signals:

1. Review covers a stale revision.
2. The parent accepts a child's success claim without running the verifier.
3. Delegation loops indefinitely or duplicates completed investigation.
