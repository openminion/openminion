# `modules/task/`

Owner: `openminion-task`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

The agent's task/plan substrate: typed task records, plan drafts and revisions,
decision digests, pending actions, project checkpoints, autonomy proof records,
and the lifecycle state machine that moves tasks from `proposed` → `accepted`
→ `in_progress` → `done`/`failed`. It owns the durable resume and replay
contracts used to continue interrupted work across runs.

## Scope

- `TaskCtlInterface`, `InMemoryTaskCtl`, and `SqlTaskCtl`
- Records: `TaskRecord`, `PlanRecord`, `PlanStepRecord`,
  `TaskLifecycleRecord`, `TaskEvent`, `TaskDigest`, `TaskDigestTask`
- Drafts: `PlanDraft`, `PlanStepDraft`
- Inputs: `TaskCreateInput`, `StepUpdateInput`
- Status enums: `TaskStatus`, `PlanStepStatus`,
  `TaskLifecycleState`
- Operations: `TaskOp`, `TaskOps`
- Resume contract: `ResumePointer`
- Pending actions: `PendingAction`
- Lifecycle helpers: `TaskLifecycleRepository`
- Manager (top-level orchestration): `TaskManager`
- Project controls: objectives, budgets, permissions, checkpoints, cycle
  records, capability reports, outcome reports, and replay commands
- Autonomy evidence: run records, continuation policy, command/test evidence,
  and terminal proof packets
- Errors: `TaskError`, `TaskNotFoundError`, `StepNotFoundError`,
  `PlanNotFoundError`, `PendingActionNotFoundError`

## Non-goals

- Tool execution that a task step triggers (lives in `modules/tool/`)
- Cross-agent task delegation (lives in `modules/a2a/`)
- Reasoning that produces plans (lives in `modules/brain/`)

## Public surface

`openminion.modules.task` re-exports the task, lifecycle, project-control,
checkpoint/replay, reporting, and autonomy-evidence contracts used by runtime
consumers. The exact supported export list lives in `__init__.py`; downstream
code should use that facade instead of reaching into implementation packages.

## Dependencies

- `modules/storage/` — SQLite backing store
- `base/` — config, runtime
- Versioning: `TASK_INTERFACE_VERSION`, `ensure_task_compatibility`

## Canonical shape

Canonical with `interfaces.py`, `schemas/`, `events.py` (explicit
event surface), `runtime/` subpackage, `storage/` subpackage, `cli.py`.
Unusually for openminion, this module DOES use an explicit `events.py`
file — most modules embed events in model or schema owners. The
explicit split here matches the task substrate's audit-trail
requirements.
