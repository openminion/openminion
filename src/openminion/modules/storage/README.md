# `modules/storage/`

Owner: `openminion-storage`
Shape: `engine-owning`
Runtime peer: standalone (no `services/` peer)

## Purpose

The shared persistence substrate for every module that needs durable
state. Owns the `RecordStore` / `BlobStore` / `VectorStore` Protocols,
their SQLite and Postgres backends, the migration runner, capability
detection, schema-drift detection, integrity verification, and the
runtime helpers (path resolution, idempotency, session/cron stores)
that wrap those backends for consumer modules.

## Scope

- `StorageEngine`, `BaseModuleStore`, `BaseModuleSQLiteStore`,
  `ModuleStorage`
- Store Protocols + interfaces: `RecordStoreInterface`,
  `BlobStoreInterface`, `StructuredStoreInterface`,
  `VectorStoreInterface`, `HybridStoreInterface`
- Concrete backends (`backends/`): `RecordStoreSQLite`,
  `BlobStoreFS`, `HybridStore`, `ZvecVectorStore`, `NoopVectorStore`,
  plus Postgres variants
- Capability detection + envelope errors:
  `CapabilityRequirement`, `UnsupportedCapabilityError`,
  `create_capability_error_envelope`, `check_capability_support`
- Migrations: `MigrationRunner` and module metadata helpers
- Drift detection: `detect_schema_drift`, `SchemaDriftReport`,
  `SchemaDriftKind`, and the operator-authored `RUNTIME_ONLY_TABLES`
  allowlist for backend-created sidecar tables
- Repair / backup: `verify_module_integrity`, `repair_module_db`,
  `backup_module_db`, `restore_module_db`
- Integrity hashes: optional SHA-256 record hashes and verification
  outcomes for stores that opt into row integrity checks
- Runtime helpers (`runtime/`): SQLite path resolution, idempotency
  store, memory-record store, session store

## Non-goals

- Module-specific business logic (the consuming module owns it)
- High-level transaction orchestration across multiple stores
- Cross-backend data migration (each backend manages its own
  migrations)

## Public surface

Re-exported from `openminion.modules.storage`:

- Engine: `StorageEngine`, `StorageEngineConfig`, `ModuleStorage`,
  `BaseModuleStore`, `BaseModuleSQLiteStore`
- Records: `RecordStore`, `RecordStoreInterface`, `RecordStoreSQLite`,
  `RowRef`, `EventRef`
- Blobs: `BlobRef`, `BlobStore`, `BlobStoreFS`, `BlobStoreInterface`
- Hybrid: `HybridStore`, `HybridStoreInterface`
- Vectors: `VectorStoreInterface`, `ZvecVectorStore`, `NoopVectorStore`
- Backends: `BackendDescriptor`, `BackendRegistry`,
  `default_backend_registry`, `resolve_storage_provider`
- Migrations / integrity: `MigrationRunner`, `verify_module_integrity`,
  `repair_module_db`, `backup_module_db`, `restore_module_db`,
  `ReindexReport`
- Capability: `CapabilityRequirement`, `UnsupportedCapabilityError`,
  `create_capability_error_envelope`, `check_capability_support`
- Envelope: `StorageEnvelope`, `StorageError`,
  `STORAGE_INTERFACE_VERSION`, `StructuredStoreInterface`,
  `ensure_interface_compatibility`

## Dependencies

- `base/` — config, paths, errors
- `services/cron.*` (approved per CTCR-05) for cron-driven store
  background tasks

## Canonical shape

Canonical with `interfaces.py`, `engine.py`, `record_store.py`,
`backends/` subpackage, `runtime/` subpackage, `migrations/` subpackage,
`cli.py`. The module's deliberate complexity (38 public symbols) is
load-bearing — every other module's persistence delegates here, so the
public surface intentionally exposes every primitive consumers need.
