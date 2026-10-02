# Continuity Scenarios

## `LH-CONT-001` Pause And Restart Recovery

- Version: 1
- Tags: memory, context, session, durable-plan, restart-recovery
- Prerequisites: durable project state and permission to stop and restart the process

Prompt:

> Investigate one cross-module reliability weakness in this repository, create a durable plan with acceptance criteria and evidence references, complete the research and diagnosis stages, then pause at a clean checkpoint before implementation; after the process is restarted and the instruction `resume the active project from durable state` is sent, restate the original objective, completed evidence, unresolved decisions, and next incomplete step, then continue through implementation and verification without reconstructing facts from guesses or repeating completed work.

Pass evidence:

1. The pre-pause checkpoint records objective, evidence, progress, and next action.
2. Restart uses persisted state rather than conversation-only recall.
3. Important constraints and evidence survive intact.
4. Work resumes at the next incomplete step and reaches a verified terminal state.

Failure signals:

1. Lost objective, sources, constraints, or completed-step state.
2. Repeated research or implementation after restart.
3. Completion inferred from stale state instead of fresh verification.

## `LH-CONT-002` Long Context Instruction Retention

- Version: 1
- Tags: context, instruction-retention, drift, final-verification
- Prerequisites: a repository and enough runtime budget for a multi-stage task

Prompt:

> Complete a multi-stage assessment of this repository covering architecture, tests, runtime behavior, and one real user workflow while preserving these non-negotiable constraints throughout the run: remain read-only, do not install dependencies, use no more than three delegated tasks, keep generated evidence outside the repository, and distinguish deterministic proof from live or external evidence; revise the plan as needed, then verify every constraint and the original objective explicitly before finishing.

Pass evidence:

1. Constraints remain active after long tool and context sequences.
2. Generated artifacts stay outside the repository.
3. Delegation never exceeds the stated bound.
4. Final verification checks the original objective and every constraint.

Failure signals:

1. Late-stage context drift or unauthorized mutation.
2. Deterministic tests presented as live acceptance.
3. A polished summary that omits violated constraints.
