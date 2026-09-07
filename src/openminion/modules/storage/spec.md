# Storage interface contract

Status: active
Last updated: 2026-09-06

The canonical contract version is `STORAGE_INTERFACE_VERSION = "v1"`.
`src/openminion/modules/storage/interfaces.py` is authoritative for exact
method signatures.

## Built-in backend IDs

- `record.sqlite`
- `record.postgres`
- `blob.fs`
- `vector.zvec`
- `vector.noop`

`BackendRegistry` can register additional record, blob, or vector factories.
Unknown IDs fail explicitly.

## Interfaces

- `RecordStoreInterface`: SQL-style transactions, execution, row helpers,
  health, migration, checkpoint, capability, and diagnostics methods
- `BlobStoreInterface`: byte/file writes, reads, metadata, garbage collection,
  verification, health, and backend description
- `VectorStoreInterface`: upsert, search, delete, namespace inspection, count,
  health, and backend description
- `StructuredStoreInterface`: typed CRUD, batches, queries, transactions,
  health, and backend description
- `HybridStoreInterface`: combined blob, event, row, list, and status methods
- `ModuleStorageOpsInterface`: detect, verify, backup, restore, migrate, export,
  and rehydrate lifecycle operations for one module-owned database

`ensure_interface_compatibility()` validates required structural methods for a
named interface. Capability checks remain separate so optional behavior does
not silently become a base-interface requirement.

## Engine usage

Modules use a namespaced `ModuleStorage` from one `StorageEngine`:

```python
from openminion.modules.storage import StorageEngine

engine = StorageEngine.from_paths(
    root_dir="./.openminion/storage/blob",
    sqlite_path="./.openminion/storage/storage.db",
    vector_backend="vector.zvec",
)
memory = engine.module("memory")

memory.sql_execute(
    "CREATE TABLE IF NOT EXISTS memory_records "
    "(id TEXT PRIMARY KEY, summary TEXT)"
)
memory.sql_execute(
    "INSERT INTO memory_records(id, summary) VALUES (?, ?)",
    ("m1", "hello"),
)
rows = memory.sql_query(
    "SELECT id, summary FROM memory_records WHERE id = ?",
    ("m1",),
)
```

Configure `vector_backend=None` when vector storage is not needed. Use
`vector.noop` only when an explicit registered no-vector backend is useful to a
caller.

## Module data responsibilities

Each module that persists data must:

1. own its logical schema and versioning,
2. use the storage planes instead of reimplementing an engine,
3. own migration and upgrade behavior,
4. provide the portability operations its public contract promises,
5. preserve compatible on-disk data through migrations.

`StorageEnvelope` carries structured success or `StorageError` /
`UnsupportedCapabilityError` facts. It does not replace the domain result
models owned by consuming modules.
