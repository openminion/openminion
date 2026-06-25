# `services/runtime/`

Owner: services-layer
Pairs with: standalone (no `modules/runtime/` peer in this layer —
`modules/runtime/` exists as a separate feature owner; this package is
the cross-owner runtime orchestration that ties the whole system
together)

## Purpose

Top-level runtime composition. Owns the `OpenMinionRuntime` composed
runtime, the `AgentRuntimeManager` that supervises live agents, the
runtime daemon, the CLI entry, the cron-delivery bridge, the canonical
`build_*` factories that wire each service together, and the discovery
+ plugin loader for runtime extensions.

This is the place an operator boots the system from. `python -m
openminion.services.runtime` is the daemon entrypoint.

## Public surface

Re-exported from `openminion.services.runtime`:

- Composition: `OpenMinionRuntime`
- Manager: `AgentRuntimeManager`, `AgentHandle`, `AgentStatus`,
  `TurnHandle`, `TurnRequest`, `TurnResponse`, `TurnChunk`,
  `TurnError`, `TurnTelemetry`, `ToolCallSummary`
- Daemon: `build_runtime_manager(...)`, `build_turn_request(...)`
- Config: `RuntimeConfig` (`ManagerConfig` remains as a compatibility alias)
- Status: `RunStatus`
- Env: `apply_runtime_environment(...)`
- Version: `__version__`

Canonical factories (in `bootstrap.py`):

- `build_action_policy_service(...)`
- `build_session_context_service(...)`
- `build_agent_memory_service(...)`
- `build_gateway_service(...)`
- `build_brain_runner_bundle(service)`
- `build_agent_runtime_service(...)`

Internal modules of note:

- `composition.py` — `OpenMinionRuntime`
- `bootstrap.py` — the `build_*` factories
- `manager.py` — `AgentRuntimeManager` and turn records
- `daemon.py` — daemon process entry
- `cli.py`, `__main__.py` — operator CLI
- `engine.py` — `RuntimeEngine` (policy + tool dispatch)
- `discovery.py`, `plugins/` — runtime plugin loader
- `cron/` — cron <-> turn delivery, execution, and audit helpers
- `turn_router.py` — turn routing across composed surfaces
- `ingress.py`, `lifecycle.py` — runtime ingress + lifecycle hooks
- `catalog.py` — runtime catalog
- `env.py` — `apply_runtime_environment`
- `verifier_binding.py` — verifier binding for security checks
- `contracts/` — runtime contracts subpackage

## Owned objects

- `OpenMinionRuntime` instance (single composition root).
- `AgentRuntimeManager` (supervises live agent handles).
- Runtime plugins registry.
- Daemon process state.

## Non-goals

- Feature ownership — every feature still lives in `modules/`.
- Service-specific composition — each service owns its own internal
  composition. `services/runtime/` only stitches them.
- Operator commands beyond the runtime daemon — additional verbs
  belong on `controlplane/` or per-service CLIs.

## Dependencies

- Every other `services/` package (this is the composition root).
- `modules/` — feature owners.
- `base/` — config, paths, runtime, errors, channel primitives.

## How this differs from `modules/`

`modules/runtime/` (if/when present) owns runtime-flavored module
primitives. `services/runtime/` is the cross-owner composition root —
the place where the whole system is wired and launched. Per the
`services/README.md` naming note: `services.runtime` means cross-owner
system orchestration, distinct from `openminion.base.runtime` and
from any `<module>/runtime/` package.
