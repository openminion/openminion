# `services/agent/`

Owner: services-layer
Pairs with: `modules/brain/` (LLM-first decision loop)
Canonical builder: `services/runtime/bootstrap.py:build_agent_runtime_service`

## Purpose

Runtime orchestration for an agent's conversational turn. `AgentService`
is the composed runtime peer that holds the LLM provider wiring, hook
fabric, identity binding, memory adapter, tool fallbacks, and
turn-context plumbing required to drive a brain turn against a session
record. The package converts session-shaped input into provider-shaped
history, runs the configured tool-call strategy, applies hook validation,
and emits the agent-side telemetry events.

## Public surface

Re-exported from `openminion.services.agent`:

- `AgentService` — turn entry point (mixes `AgentTurnFlowMixin` from
  `execution/`)
- Helpers: `_history_role`, `_looks_like_tool_call_envelope_text`,
  `_loop_tool_feedback`, `_map_history_to_provider`,
  `_provider_tool_call_strategy`, `_resolve_system_prompt`
- Constants: `_DEFAULT_TOOL_LOOP_CONTINUE_PROMPT`

Internal modules of note:

- `service.py` — `AgentService`
- `execution/` — turn flow (`flow.py`), composition, tool planning, lane
  runners (`required/`, `unforced/`), validators, and finalization helpers
- `hooks.py` — hook fabric
- `lifecycle.py` — lifecycle event registry and settings-driven lifecycle hooks
- `identity.py`, `identity_binding.py` — identity binding at turn time
- `memory/` — memory retrieval, extraction, and turn-recording helpers
- `prompt_history.py` — provider-shaped prompt-history assembly
- `telemetry.py` — agent-side event emission
- `fallbacks.py` — fallback tool catalog when provider catalog is empty
- `turn_context.py` — per-turn context container
- `context.py` — builds the system context for a turn

## Owned objects

- `AgentService` instance (composed once per runtime)
- Per-turn `TurnContext` records
- Provider-history adapter state for the active LLM call

## Non-goals

- LLM decision logic (owned by `modules/brain/`)
- Tool catalogue or executor (owned by `modules/tool/`)
- Session persistence schema (owned by `modules/session/`)
- Identity record schema (owned by `modules/identity/`)
- Channel transport (owned by `services/gateway/` + `services/channel/`)

## Dependencies

- `modules/brain/` — runner contracts and decision adapters
- `modules/llm/` — provider tool-call strategy
- `modules/session/` — session record shape
- `modules/memory/` — memory hand-off adapter
- `modules/tool/` — tool registry + executor
- `services/runtime/bootstrap.py` — composition root

## How this differs from `modules/`

`modules/brain/` owns the LLM-first decision policy; `services/agent/`
owns the runtime that hands a session turn to that policy and wires
the providers, hooks, memory, identity, and tool adapters around it.
This package contains no decision logic, only composition and
transport-shaped glue.
