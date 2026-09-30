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
- Wire types: `A2AObservabilityContext`, `AgentDescriptor`, `ArtifactRef`,
  `Envelope`, `JobRecord`
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

The turn bridge, synchronized runtime cache, and APIRuntime-owned close path are
implemented. Package-owned conformance, request/auth, concurrent lifecycle,
and local HTTP tests cover the bounded endpoint. Public readiness claims should
still say "authenticated local external A2A v1 preview"; third-party peer
certification remains separate.

A2A audit storage keeps structural correlation fields for 14 days by default.
Set `a2a.storage.audit.capture_payloads=true` only when detailed task payloads
are required: payload tracing can be large and may contain sensitive task
content. `a2a.storage.audit.retention_days` controls the active window, while
`a2a.storage.audit.archive_retention_days=0` disables compressed SQLite
archives. OpenMinion accepts this existing module configuration at the top
level of its config file; no duplicate runtime setting is required.

Operators can inspect the current agent/session's newest delegations with
`openminion agent delegate-list --limit 20` or `/delegate list 20`. The limit
defaults to 20 and cannot exceed 200. This view contains structural task and
trace fields only; it is not a transcript or result-payload cache.

## Dependencies

- `modules/registry/` — agent descriptor / route resolution
- `modules/storage/` — backend store primitives
- `base/` — config / channel / errors primitives

## Canonical shape

The module follows the canonical pattern with one naming variant: the
service file is `runtime.py` (not `service.py`). This convention is
shared with several other modules where a runtime-coordinator owner
fits the responsibility better than a "service" framing.
