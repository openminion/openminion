# `modules/controlplane/`

Owner: `openminion-controlplane`
Shape: `engine-owning`
Runtime peer: standalone (no `services/` peer)

## Purpose

The runtime control plane: channel adapters (telegram, console, A2A,
etc.), inbound/outbound message contracts, dispatcher/router, persistent
inbox/outbox queues, and command resolution. Owns the path from "an
external message arrives" to "the agent runtime begins a turn" and back.

## Scope

- Channel adapters and registry (`channels/`, `adapters/`, `wizard/`, `runtime/`)
- Inbound/outbound dispatcher, router, inbox/outbox workers
- Persistent control-plane stores (`storage/` — SQLite + in-memory)
- Typed control-plane envelopes (`contracts/`)
- Cron-delivery infrastructure for scheduled wake-ups

## Non-goals

- Per-message tool execution (lives in `modules/tool/`)
- Agent reasoning / turn logic (lives in `modules/brain/`)
- Memory persistence (lives in `modules/memory/`)
- The TUI rendering layer

## Public surface

Re-exported from `openminion.modules.controlplane`:

- Wire contracts: `InboundMessage`, `OutboundMessage`, `DeliveryContext`,
  `ResolvedContext`, `CommandResult`
- Runtime: `RuntimeCoordinator`, `ChannelRegistry`, `ControlPlaneDispatcher`,
  `InboxWorker`, `OutboxWorker`, `Router`
- Storage: `InMemoryControlPlaneStore`, `SQLiteControlPlaneStore`
- Versioning: `CONTROLPLANE_INTERFACE_VERSION`,
  `ensure_controlplane_component_compatibility`

## Dependencies

- `modules/a2a/` for agent-to-agent channel
- `modules/session/` for session/turn lifecycle handoff
- `modules/registry/` for agent resolution
- `services/cron.*` (approved shared-service path per CTCR-05) for
  scheduled-delivery infrastructure
- `base/` — channel, config, runtime, errors

## Canonical shape

Canonical with `interfaces.py`, `contracts/` subpackage, `runtime/`
subpackage, `storage/` subpackage, and `cli.py`. No `schemas.py` —
typed envelopes live in `contracts/` instead. The `adapters/client.py`
cross-layer import is a documented exception (MSB-01) — it is the
deliberate bridge that constructs the wrapped `OpenMinionRuntime`.
