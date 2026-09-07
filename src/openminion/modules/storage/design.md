# Storage interface design

Status: active
Last updated: 2026-09-06

OpenMinion storage separates record, blob, vector, structured, and hybrid
planes behind small Protocol contracts. Modules own their logical schemas and
migrations; the storage module owns backend selection, shared persistence
mechanics, and compatibility checks.

## Design goals

1. Preserve the `v1` storage interface while adding capabilities without
   forcing every backend to implement every plane.
2. Keep backend choice explicit through stable backend IDs.
3. Reject missing required capabilities before a feature uses them.
4. Keep module data ownership with the consuming module.
5. Support SQLite and filesystem defaults without preventing PostgreSQL or
   vector backends.

## Current structure

- `interfaces.py` owns Protocols, descriptors, requirements, envelopes, and
  compatibility checks.
- `engine.py` composes configured record, blob, vector, and hybrid stores.
- `backends/registry.py` owns backend factories and built-in IDs.
- `record_store.py` and `backends/` own concrete stores.
- `migrations/` owns storage lifecycle and migration records.
- `runtime/` owns module-store helpers, integrity, backup/restore, provider
  selection, vector synchronization, and session-store integration.
- `telemetry.py` owns the storage telemetry hook contract.

## Capability rules

`BackendDescriptor` reports a backend's identity, planes, capabilities, and
limits. `CapabilityRequirement` states one feature requirement.
`check_capability_support()` returns the structured compatibility result, while
`create_capability_error_envelope()` builds an explicit error envelope for a
mismatch.

Capabilities are configuration and implementation facts. The runtime does not
infer them from provider names or failed operations.

## Ownership rules

- A consuming module owns its tables, records, migration definitions, and
  import/export meaning.
- Storage owns connection behavior, backend construction, shared integrity,
  backup/restore primitives, and common telemetry.
- Cross-module transactions are not provided.
- Backend-specific SDKs stay behind backend factories.
- New abstractions belong here only when at least two storage consumers share
  the same mechanical contract.
