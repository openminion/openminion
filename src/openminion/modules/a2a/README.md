# `modules/a2a/`

Owner: `openminion-a2a`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

Agent-to-agent messaging substrate: envelope transport, job records,
agent descriptors, and the storage/audit primitives that record A2A
traffic. The module is the canonical owner of the wire-level contract
between agents.

## Scope

- Wire-level envelope and job record types (`models.py`)
- Transport adapters (`transport/`) and storage backends (`storage/`)
- The `A2ARuntime` orchestration class and its versioned interface
- Audit-style persistence of A2A events for replay and operator review

## Non-goals

- Cross-agent identity issuance (lives in `modules/identity/`)
- Routing policy beyond the wire contract (`modules/registry/` owns
  agent resolution; this module just consumes resolved addresses)
- High-level workflow orchestration on top of A2A messages

## Public surface

Re-exported from `openminion.modules.a2a`:

- `A2ARuntime`, `A2ARuntimeInterface`, `A2A_INTERFACE_VERSION`,
  `ensure_a2a_compatibility`
- Wire types: `AgentDescriptor`, `ArtifactRef`, `Envelope`, `JobRecord`
- Config: `RuntimeConfig`, `load_config`

## Dependencies

- `modules/registry/` — agent descriptor / route resolution
- `modules/storage/` — backend store primitives
- `base/` — config / channel / errors primitives

## Canonical shape

The module follows the canonical pattern with one naming variant: the
service file is `runtime.py` (not `service.py`). This convention is
shared with several other modules where a runtime-coordinator owner
fits the responsibility better than a "service" framing.
