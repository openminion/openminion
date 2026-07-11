# Agent Memory Service Boundary

`openminion.services.agent.memory` is the agent-turn integration boundary for the
canonical memory module. It may wire module-owned memory APIs into agent/runtime
objects, but it must not own memory-domain policy.

Allowed residual files:

1. `__init__.py` — stable service-facing exports for the gateway adapter and
   memory capsule constants.
2. `capsule.py` — service/runtime configuration snapshot helpers used to attach
   memory policy metadata to agent turns.
3. `gateway_adapter.py` — the gateway-facing adapter that composes public
   module-owned memory surfaces for the agent runtime.

Disallowed here:

1. extraction policy, text parsing, or semantic memory mining,
2. retrieval ranking or retrieval-filter ownership,
3. learning, candidate readiness, skill-promotion, or trust policy,
4. storage, diagnostics, or debug-export ownership,
5. context assembly or context-packing policy.

Those concerns belong under `openminion.modules.memory` or another explicit
module owner. `make lint` runs `scripts.validate.memory_boundary`
to prevent this package from regrowing into a second memory subsystem.
