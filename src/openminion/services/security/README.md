# `services/security/`

Owner: services-layer
Pairs with: standalone (no `modules/security/`)
Canonical builders: `build_execution_boundary_policy_adapter`,
`build_default_composition_boundary_adapter`

## Purpose

Runtime authorization fabric. Owns the security policy engine that
gates tool invocations, the tool-budget policy that bounds per-turn
tool usage, the untrusted-content sanitizer applied to model output
and tool output, the plugin-trust policy that gates plugin
activation, and the boundary adapters wired into the runtime
composition root.

## Public surface

Re-exported from `openminion.services.security`:

- Policy engine: `SecurityPolicyEngine`, `SecurityPolicyAction`,
  `SecurityPolicyCheck`, `SecurityPolicyContext`
- Decision constants: `DECISION_ALLOW`, `DECISION_REQUIRE_APPROVAL`
- Budget: `ToolBudgetPolicy`, `ToolBudgetState`
- Actors: `default_internal_actor`
- Plugin trust: `derive_plugin_activation_risk`,
  `evaluate_plugin_trust_policy`
- Validation: `run_security_validate`
- Untrusted-content: `sanitize_untrusted_content`, `safe_tag`
- Boundary adapters: `ExecutionBoundaryPolicyAdapter`,
  `build_execution_boundary_policy_adapter`,
  `build_default_composition_boundary_adapter`

Internal modules:

- `policy.py` — engine + budget
- `blast_radius/adapter.py`, `blast_radius/wiring.py` — blast-radius
  policy adapter
- `tool_execution.py` — execution-boundary adapter
- `untrusted_content.py` — sanitizer + safe-tag helpers
- `validate.py` — operator-facing validation entry

## Owned objects

- `SecurityPolicyEngine` runtime instance.
- `ToolBudgetState` per-turn records.
- Boundary adapters composed into the runtime.

## Non-goals

- Identity record schema — owned by `modules/identity/`.
- Tool catalog — owned by `modules/tool/`.
- Channel authenticity — owned by `services/channel/`.
- Secret rotation / DR — currently undocumented; see operational
  backlog.

## Dependencies

- `modules/identity/` — actor records.
- `modules/tool/` — tool catalog and risk-tier metadata.
- `modules/brain/` — escalation gate (`pending_approval_decision`).
- `services/runtime/` — composes adapters at bootstrap.

## How this differs from `modules/`

There is no `modules/security/`. Security is a cross-cutting runtime
concern that consumes identity (who), tool (what), and brain
(approval gate) module surfaces and composes them into a single
policy decision at execution time. The policy logic itself lives
here, not in any individual module.
