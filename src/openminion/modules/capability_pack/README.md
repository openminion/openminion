# Capability Pack Module

Owner: `openminion-capability-pack`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

Own typed capability-pack manifests, policy profiles, registry lookup,
activation resolution, and audit records. A pack narrows the tools and skills
already available to a session; it does not grant capabilities or execute work.

## Boundaries

- Skills teach workflows but do not perform side effects.
- Tools and MCP providers retain execution ownership.
- Policy owners retain allow, ask, and deny decisions.
- Activation emits canonical telemetry events without prompt or secret data.
- CLI and API modules adapt these contracts but do not redefine them.

## Public Shape

- `schemas.py`: strict manifests, policy profiles, active-pack, and audit types
- `registry.py`: manifest registration and lookup
- `resolver.py`: session-scoped activation and dependency checks
- `policy.py`: pack-local policy evaluation
- `evaluation.py`: deterministic pack evaluation contracts
- `api.py` and `cli.py`: transport-neutral views and CLI adaptation
