# `openminion.modules.session.todo`

Owner: `openminion-session`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

Owns session-scoped checklist state for the operator-visible `/plan` and
`plan.*` surfaces. This is a session-local todo owner, not the canonical
home for `TaskPlan` or any of the brain planning substrates.

## Scope

In scope:

1. Typed data model: `TodoItem`, `Todo`, `TodoItemStatus`.
2. `TodoStore` protocol + `InMemoryTodoStore` implementation.
3. Session isolation — todo state keyed by `session_id`.
4. Storage caps — bounded in-memory state with LRU eviction.
5. Deterministic exception classes whose `code` attributes preserve the
   existing v2 envelope contract strings (`PLAN_EMPTY`,
   `INVALID_PLAN_INDEX`, `INVALID_PLAN_STATUS`).

Out of scope:

1. `TaskPlan` / autonomous decomposition state.
2. Cross-session persistence.
3. Multi-agent sharing.
4. Tool wiring details (owned by `openminion.tools.todo`).

## Related

1. session todo rename execution spec
2. session plan surface deconfliction tracker
