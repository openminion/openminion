# `modules/registry/`

Owner: `openminion-registry`
Shape: `template-aligned`
Runtime peer: standalone (no `services/` peer)

## Purpose

Agent / module / plugin registry: stores agent descriptors, capabilities,
transport endpoints, and the resolver that turns a routing request into
a concrete destination. Owns the typed registry schema and the SQLite
backing store.

## Scope

- `AgentRegistry` service + `AgentRegistryInterface` Protocol
- Registry models: `AgentDescriptor`, `Capability`, `TransportEndpoint`,
  `AgentStatus`, `ResolveConstraints`, `ResolvedRoute`
- Manifest validation (`manifest.py`)
- Persistent backing store (`storage/`)

## Non-goals

- Wire-level A2A transport (lives in `modules/a2a/`)
- Authentication of registered agents (lives in `modules/identity/`)
- Plugin lifecycle / instantiation (lives in `modules/tool/bootstrap/`)

## Public surface

Re-exported from `openminion.modules.registry`:

- Service: `AgentRegistry`, `AgentRegistryInterface`, `AgentRegistryConfig`,
  `StoreConfig`, `load_config`
- Records: `AgentDescriptor`, `Capability`, `TransportEndpoint`,
  `AgentStatus`, `ResolveConstraints`, `ResolvedRoute`
- Versioning: `REGISTRY_INTERFACE_VERSION`,
  `ensure_registry_compatibility`

## Dependencies

- `modules/storage/` — SQLite store substrate
- `base/` — config, paths

## Canonical shape

**MCCS-05 decision (2026-05-08):** the module IS canonically compliant.
The public facade lives in `agents.py`, which keeps the owner specific without
repeating the package context or falling back to the old `registry/registry.py`
shape, while preserving the public type identity (`AgentRegistry`).
