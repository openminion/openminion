# `modules/task/`

Owner: `openminion-task`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

The agent's task/plan substrate: typed task records, plan
drafts/records/step lists, decision digests, pending-action queue, and
the lifecycle state machine that moves tasks from `proposed` →
`accepted` → `in_progress` → `done`/`failed`. Owns the resume-pointer
contract that lets the agent pick up an interrupted task across runs.

## Scope

- `TaskCtl` / `TaskCtlInterface` (in-memory and SQL-backed)
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
- Errors: `TaskError`, `TaskNotFoundError`, `StepNotFoundError`,
  `PlanNotFoundError`, `PendingActionNotFoundError`

## Non-goals

- Tool execution that a task step triggers (lives in `modules/tool/`)
- Cross-agent task delegation (lives in `modules/a2a/`)
- Reasoning that produces plans (lives in `modules/brain/`)

## Public surface

Re-exported from `openminion.modules.task` — 32 symbols (see Scope).
The breadth reflects that downstream consumers (brain, controlplane,
session) interact with multiple task subtypes directly.

## Dependencies

- `modules/storage/` — SQLite backing store
- `base/` — config, runtime
- Versioning: `TASK_INTERFACE_VERSION`, `ensure_task_compatibility`

## Canonical shape

Canonical with `interfaces.py`, `schemas.py`, `events.py` (explicit
event surface), `runtime/` subpackage, `storage/` subpackage, `cli.py`.
Unusually for openminion, this module DOES use an explicit `events.py`
file — most modules embed events in `models.py` / `schemas.py`. The
explicit split here matches the task substrate's audit-trail
requirements.
