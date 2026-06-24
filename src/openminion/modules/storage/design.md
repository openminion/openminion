# OpenMinion Storage Interface Contract Design - v2

## Design Rationale

### Goals

1. **Backward Compatibility**: Ensure existing v1 implementations continue to work without changes
2. **Enhanced Type Safety**: Provide better static type checking for storage interfaces
3. **Capability-Based Features**: Enable explicit capability negotiation between clients and backends
4. **Modular Interface Contracts**: Separate concerns into focused, composable interfaces

### Architecture Decisions

#### 1. Preservation of v1 Contract
- `STORAGE_INTERFACE_VERSION` remains "v1" to maintain backward compatibility
- New interfaces are additive, not replacement-based
- New features marked as v1.1 through feature flags and capabilities
- Compatibility validator extended to handle new interfaces without breaking old ones

#### 2. Capability Checking Infrastructure
The system implements capability negotiation through:

- `BackendDescriptor`: Static declaration of supported features
- `CapabilityRequirement`: Client expression of required features  
- `check_capability_support()`: Runtime compatibility verification
- `UnsupportedCapabilityError`: Explicit error signaling for mismatch cases

#### 3. Interface Separation
The monolithic storage interface is split into logical domains:

- `StructuredStoreInterface`: Typed record/row operations
- `VectorStoreInterface`: Embedding/semantic operations  
- `RecordStoreInterface`: Low-level SQL operations (existing)
- `BlobStoreInterface`: Binary data storage (existing)
- `HybridStoreInterface`: Composite operations (existing)

#### 4. Type Hints and Protocols
- All interfaces use `typing.Protocol` for structural typing
- Runtime checking via `@runtime_checkable` decorator
- Consistent return type annotations across similar operations
- Proper generics usage for collections and type safety

### Implementation Details

#### Feature Flags Implementation
New interfaces should support soft capability requirements through feature flags stored in the `BackendDescriptor.capabilities` field. Examples:

- "structured_crud": Standardized structured data operations
- "vector_upsert_atomic": Atomic upsert operations in vector stores
- "transaction_nesting": Support for nested transactions
- "search_filters": Advanced filtering in vector search

#### Interface Versioning
- Contract version: Remains "v1" for all existing compatibility
- Feature version: Capabilities field can specify versions like `{"structured_crud": "1.1"}`  
- Soft compatibility: New features should be detectable before invocation

#### Error Classification
Distinguish between capability errors and operational errors:

- `StorageError`: Generic operational/storage errors (network, permission, etc.)
- `UnsupportedCapabilityError`: Clear feature/compatibility mismatches
- `ContractVersionError`: Interface contract violations (deprecated)

### Integration Path

#### Phase 1: Core Definitions
- Define new protocols in interfaces.py
- Add capability checking functions
- Maintain all existing interfaces

#### Phase 2: Test Coverage  
- Verify existing interfaces continue working
- Validate new interface contracts
- Test capability mismatch scenarios

#### Phase 3: Implementation Guidelines
- Document migration path for implementers
- Provide abstract base classes/helpers for building storage backends
- Sample implementations of new protocols