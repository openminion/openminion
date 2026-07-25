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

## Current maturity

`modules/a2a` owns the in-process A2A runtime, storage/audit primitives, and
Google A2A v1 Agent Card, JSON-RPC, and task DTOs. OpenMinion now exposes a
bounded external v1 route through the API server:

- `GET /.well-known/agent.json` returns public Agent Card metadata.
- `POST /a2a/v1/jsonrpc` requires `Authorization: Bearer <token>` with
  `OPENMINION_A2A_BEARER_TOKEN` configured.
- Supported JSON-RPC methods are `tasks/send`, `tasks/get`, and `tasks/cancel`.
- Task streaming is not enabled in v1; the task-events route fails closed with a
  typed unsupported response and the Agent Card reports `streaming=false`.

External endpoint scope and validation evidence are tracked in
`docs/trackers/qa/openminion-external-a2a-network-endpoint-2026-07-24-tracker.md`.
Public readiness claims should say "authenticated local external A2A endpoint"
until third-party peer interoperability is separately proven.

## Dependencies

- `modules/registry/` — agent descriptor / route resolution
- `modules/storage/` — backend store primitives
- `base/` — config / channel / errors primitives

## Canonical shape

The module follows the canonical pattern with one naming variant: the
service file is `runtime.py` (not `service.py`). This convention is
shared with several other modules where a runtime-coordinator owner
fits the responsibility better than a "service" framing.
