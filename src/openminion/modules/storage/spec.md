# OpenMinion Storage Interface Contract - Spec (v2)

## Overview

This specification establishes the v2 interface contract for OpenMinion storage backends, designed to be backward-compatible with v1 while introducing enhanced abstractions and capability checking.

## Default Backend Targets

Current default backend identifiers exposed by storage engine registry:

1. `record.sqlite` for structured SQL data.
2. `blob.fs` for blob/artifact storage.
3. `vector.zvec` for vector embeddings/search.
4. `vector.noop` for explicit no-vector mode.

## Module Consumption Pattern

Modules should use `StorageEngine.module(<namespace>)` as the storage access point:

```python
engine = StorageEngine.from_paths(
    root_dir="~/.openminion/storage/blob",
    sqlite_path="~/.openminion/storage/storage.db",
    vector_backend="vector.zvec",
)
module_store = engine.module("memory")

# Structured SQL data
module_store.sql_execute("CREATE TABLE IF NOT EXISTS memory_records(id TEXT PRIMARY KEY, summary TEXT)")
module_store.sql_execute("INSERT INTO memory_records(id, summary) VALUES (?, ?)", ("m1", "hello"))
rows = module_store.sql_query("SELECT id, summary FROM memory_records WHERE id = ?", ("m1",))

# Vector data
module_store.vector_upsert(vectors=[[0.1, 0.2]], metadata=[{"scope": "global"}], ids=["m1"])
hits = module_store.vector_search(query_vector=[0.1, 0.2], top_k=5)
```

## Module Responsibilities (When Owning Data)

When another module owns persisted data and uses storage-core:

1. The module owns logical data model design and versioning.
2. The module must use storage-core planes (`record.sqlite`, `vector.zvec`) instead of custom engine reimplementation.
3. The module owns migration definitions and upgrade behavior for its data.
4. The module must provide raw export/import paths for disaster recovery.
5. Backward data continuity is required through migration; incompatible on-disk changes without migration are not allowed.

## Interfaces Defined

### Core Interface Types

#### StructuredStoreInterface
A typed CRUD/batch/query abstraction for structured data:

```python
@runtime_checkable
class StructuredStoreInterface(Protocol):
    contract_version: str
    
    # CRUD operations
    def create(table: str, data: dict[str, Any]) -> Any: ...
    def read(table: str, id_value: Any) -> dict[str, Any]: ...
    def update(table: str, id_value: Any, data: dict[str, Any]) -> Any: ...
    def delete(table: str, id_value: Any) -> bool: ...

    # Batch operations  
    def batch_create(table: str, items: List[dict[str, Any]]) -> List[Any]: ...
    def batch_read(table: str, ids: List[Any]) -> List[dict[str, Any]]: ...
    def batch_update(table: str, updates: List[tuple[Any, dict[str, Any]]]) -> List[bool]: ...
    def batch_delete(table: str, ids: List[Any]) -> List[bool]: ...

    # Query operations
    def query(table: str, filters: dict[str, Any]) -> List[dict[str, Any]]: ...
    def count(table: str, filters: dict[str, Any]) -> int: ...

    # Transactions
    def begin_transaction() -> Any: ...
    def commit_transaction(tx_handle: Any) -> None: ...
    def rollback_transaction(tx_handle: Any) -> None: ...

    # Lifecycle & diagnostics
    def healthcheck() -> dict[str, Any]: ...
    def describe_backend() -> BackendDescriptor: ...
```

#### VectorStoreInterface
Operations for embedding-based search and retrieval:

```python
@runtime_checkable
class VectorStoreInterface(Protocol):
    contract_version: str

    def upsert(vectors: List[List[float]], metadata: List[Dict[str, Any]], 
               ids: List[str], namespace: Optional[str] = None) -> None: ...
    def search(query_vector: List[float], top_k: int = 10, 
               filters: Optional[Dict[str, Any]] = None, 
               namespace: Optional[str] = None) -> List[Dict[str, Any]]: ...
    def delete(ids: List[str], namespace: Optional[str] = None) -> bool: ...
    def list_namespaces() -> List[str]: ...
    def namespace_stats(namespace: str) -> Dict[str, Any]: ...
    def count(namespace: Optional[str] = None) -> int: ...
    
    def healthcheck() -> dict[str, Any]: ...
    def describe_backend() -> BackendDescriptor: ...
    
    # v1.1: Enhanced capability checking
    def check_capability(requirement: CapabilityRequirement) -> bool: ...
```

### Capability System

#### BackendDescriptor
Describes backend capabilities:

```python
@dataclass(frozen=True)
class BackendDescriptor:
    backend_id: str
    version: str
    planes_supported: Set[Literal["record", "blob", "vector"]]
    capabilities: Dict[str, Union[str, int, bool]]
    limits: Dict[str, Union[int, str]]
```

#### CapabilityRequirement & Error Handling
Defines feature requirements and error structures:

```python
@dataclass(frozen=True)
class CapabilityRequirement:
    name: str
    version_range: str
    required_features: Set[str]

@dataclass(frozen=True)
class UnsupportedCapabilityError(Exception):
    requirement: CapabilityRequirement
    descriptor: BackendDescriptor
    message: str
```

## Versioning Strategy

- Primary `STORAGE_INTERFACE_VERSION` remains `v1` for backward compatibility
- Introduce feature flags and capability keys for `v1.1` hardening features
- Backward compatibility maintained at the API level
- Capability mismatch errors are returned explicitly in StorageEnvelope

## Contract Enforcement

Each interface provides `ensure_interface_compatibility()` function to validate implementations at runtime.
