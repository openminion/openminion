# `services/security/`

Owner: compatibility and runtime wiring
Canonical domain owner: `openminion.modules.policy`

## Purpose

Preserve required policy import paths and compose module-owned policy adapters
into the running process. Policy decisions, budgets, untrusted-content rules,
and boundary adapter behavior live in `modules/policy`.

## Public surface

`openminion.services.security` re-exports the policy contracts still consumed
by API, AECR, BPPD, CLI, and service callers. `blast_radius/wiring.py` remains
service-owned composition, while `policy.py` and `tool_execution.py` are
identity-preserving compatibility imports. `validate.py` is the
operator-facing diagnostic surface.

## Non-goals

- A second policy engine, tool budget, or untrusted-content implementation.
- Identity, tool catalog, or channel-policy domain ownership.
- New security behavior in compatibility files.
