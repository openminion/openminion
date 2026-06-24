from __future__ import annotations

from dataclasses import dataclass, field
import re
from pathlib import Path
from typing import (
    Any,
    BinaryIO,
    TYPE_CHECKING,
    Iterable,
    Literal,
    Protocol,
    runtime_checkable,
)

if TYPE_CHECKING:  # pragma: no cover
    from openminion.modules.storage.migrations.models import (
        BackupArtifact,
        DbState,
        MigrationReport,
        RehydrateReport,
        VerificationReport,
    )

STORAGE_INTERFACE_VERSION = "v1"


@dataclass(frozen=True)
class StorageError:
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": bool(self.retryable),
        }
        if self.details:
            payload["details"] = dict(self.details)
        return payload


@dataclass(frozen=True)
class BackendDescriptor:
    """Describe a backend's identity, version, and capabilities."""

    backend_id: str
    version: str
    planes_supported: set[Literal["record", "blob", "vector"]]
    capabilities: dict[str, str | int | bool]
    limits: dict[str, int | str]


@dataclass(frozen=True)
class CapabilityRequirement:
    """Describe required capabilities for a given feature."""

    name: str
    version_range: str
    required_features: set[str]


@dataclass(frozen=True)
class UnsupportedCapabilityError(Exception):
    """Error raised when backend doesn't support required capabilities."""

    requirement: CapabilityRequirement
    descriptor: BackendDescriptor
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": "UnsupportedCapabilityError",
            "requirement": {
                "name": self.requirement.name,
                "version_range": self.requirement.version_range,
                "required_features": list(self.requirement.required_features),
            },
            "descriptor": {
                "backend_id": self.descriptor.backend_id,
                "version": self.descriptor.version,
                "planes_supported": list(self.descriptor.planes_supported),
                "capabilities": self.descriptor.capabilities,
                "limits": self.descriptor.limits,
            },
            "message": self.message,
        }


@dataclass(frozen=True)
class StorageEnvelope:
    operation: str
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: StorageError | UnsupportedCapabilityError | None = None
    module: str = "openminion-storage"
    contract_version: str = STORAGE_INTERFACE_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "module": self.module,
            "contract_version": self.contract_version,
            "operation": self.operation,
            "ok": bool(self.ok),
            "data": dict(self.data),
        }
        if self.error is not None:
            payload["error"] = self.error.to_dict()
        return payload


@runtime_checkable
class StructuredStoreInterface(Protocol):
    """Typed CRUD/batch/query abstraction for structured data."""

    contract_version: str

    def create(self, table: str, data: dict[str, Any]) -> Any: ...
    def read(self, table: str, id_value: Any) -> dict[str, Any]: ...
    def update(self, table: str, id_value: Any, data: dict[str, Any]) -> Any: ...
    def delete(self, table: str, id_value: Any) -> bool: ...

    def batch_create(self, table: str, items: list[dict[str, Any]]) -> list[Any]: ...
    def batch_read(self, table: str, ids: list[Any]) -> list[dict[str, Any]]: ...
    def batch_update(
        self, table: str, updates: list[tuple[Any, dict[str, Any]]]
    ) -> list[bool]: ...
    def batch_delete(self, table: str, ids: list[Any]) -> list[bool]: ...

    def query(self, table: str, filters: dict[str, Any]) -> list[dict[str, Any]]: ...
    def count(self, table: str, filters: dict[str, Any]) -> int: ...

    def begin_transaction(self) -> Any: ...
    def commit_transaction(self, tx_handle: Any) -> None: ...
    def rollback_transaction(self, tx_handle: Any) -> None: ...

    def healthcheck(self) -> dict[str, Any]: ...

    def describe_backend(self) -> BackendDescriptor: ...


@runtime_checkable
class VectorStoreInterface(Protocol):
    """Vector store operations for embedding-based search and retrieval."""

    contract_version: str

    def upsert(
        self,
        vectors: list[list[float]],
        metadata: list[dict[str, Any]],
        ids: list[str],
        namespace: str | None = None,
    ) -> None: ...

    def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
        namespace: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def delete(self, ids: list[str], namespace: str | None = None) -> bool: ...

    def list_namespaces(self) -> list[str]: ...

    def namespace_stats(self, namespace: str) -> dict[str, Any]: ...

    def count(self, namespace: str | None = None) -> int: ...

    def healthcheck(self) -> dict[str, Any]: ...

    def describe_backend(self) -> BackendDescriptor: ...


@runtime_checkable
class RecordStoreInterface(Protocol):
    contract_version: str

    def begin(self) -> None: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def execute(self, sql: str, params: Iterable[Any] | None = None) -> Any: ...
    def executemany(self, sql: str, params: Iterable[Iterable[Any]]) -> Any: ...
    def query(self, sql: str, params: Iterable[Any] | None = None) -> list[Any]: ...
    def query_dicts(
        self, sql: str, params: Iterable[Any] | None = None
    ) -> list[dict[str, Any]]: ...
    def execute_count(self, sql: str, params: Iterable[Any] | None = None) -> int: ...
    def insert(self, table: str, row: dict[str, Any]) -> int: ...
    def query_rows(
        self,
        table: str,
        where: dict[str, Any] | None = None,
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...
    def update_rows(
        self, table: str, where: dict[str, Any], values: dict[str, Any]
    ) -> int: ...
    def delete_rows(self, table: str, where: dict[str, Any]) -> int: ...

    def healthcheck(self) -> dict[str, Any]: ...
    def migrate(self, schema_version: int) -> None: ...
    def checkpoint(self, mode: str = "PASSIVE") -> tuple[int, int, int]: ...
    def capabilities(self) -> dict[str, bool]: ...
    def last_error(self) -> str | None: ...
    def diagnostics(self) -> dict[str, Any]: ...


@runtime_checkable
class BlobStoreInterface(Protocol):
    contract_version: str

    def put_bytes(
        self,
        data: bytes,
        media_type: str = "application/octet-stream",
        ext: str = "",
        meta: dict[str, Any] | None = None,
    ) -> Any: ...

    def put_file(
        self,
        path: str,
        media_type: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> Any: ...

    def open(self, ref: Any) -> BinaryIO: ...
    def stat(self, ref: Any) -> dict[str, Any]: ...
    def gc(self, policy: dict[str, Any] | None = None) -> dict[str, Any]: ...
    def verify(self, digest: str) -> dict[str, Any]: ...

    def healthcheck(self) -> dict[str, Any]: ...

    def describe_backend(self) -> BackendDescriptor: ...


@runtime_checkable
class HybridStoreInterface(Protocol):
    contract_version: str

    def write_blob(self, *args: Any, **kwargs: Any) -> Any: ...
    def write_event(
        self, event: dict[str, Any], *, namespace: str | None = None
    ) -> Any: ...
    def write_row(
        self, table: str, row: dict[str, Any], *, namespace: str | None = None
    ) -> Any: ...
    def list_events(
        self, session_id: str, limit: int = 50, *, namespace: str | None = None
    ) -> list[dict[str, Any]]: ...
    def status(self) -> dict[str, Any]: ...


@runtime_checkable
class ModuleStorageOpsInterface(Protocol):
    """High-level storage lifecycle operations for a module-owned DB."""

    contract_version: str
    module_id: str
    module_application_id: int

    def detect(self) -> "DbState": ...
    def verify(self, *, level: str = "quick") -> "VerificationReport": ...

    def backup(self, *, mode: str | None = None) -> "BackupArtifact": ...
    def restore(
        self,
        *,
        snapshot_path: str | Path,
        target_db_path: str | Path | None = None,
    ) -> "DbState": ...

    def migrate(self, *, target: str = "head") -> "MigrationReport": ...

    def export(self, *, export_dir: str | Path) -> StorageEnvelope: ...
    def rehydrate(
        self,
        *,
        source_db_path: str | Path,
        target_db_path: str | Path,
        omx_dir: str | Path,
    ) -> "RehydrateReport": ...


_RECORD_REQUIRED = (
    "contract_version",
    "begin",
    "commit",
    "rollback",
    "execute",
    "executemany",
    "query",
    "query_dicts",
    "execute_count",
    "insert",
    "query_rows",
    "update_rows",
    "delete_rows",
    "healthcheck",
    "migrate",
    "checkpoint",
    "capabilities",
    "last_error",
    "diagnostics",
)

_VECTOR_REQUIRED = (
    "contract_version",
    "upsert",
    "search",
    "delete",
    "list_namespaces",
    "namespace_stats",
    "count",
    "check_capability",  # v1.1 capability method - not required for backward compatibility
    "healthcheck",
    "describe_backend",
)

_STRUCTURED_REQUIRED = (
    "contract_version",
    "create",
    "read",
    "update",
    "delete",
    "batch_create",
    "batch_read",
    "batch_update",
    "batch_delete",
    "query",
    "count",
    "check_capability",  # v1.1 capability method - not required for backward compatibility
    "begin_transaction",
    "commit_transaction",
    "rollback_transaction",
    "healthcheck",
    "describe_backend",
)
_BLOB_REQUIRED = (
    "contract_version",
    "put_bytes",
    "put_file",
    "open",
    "stat",
    "gc",
    "verify",
    "healthcheck",
    "describe_backend",
)
_HYBRID_REQUIRED = (
    "contract_version",
    "write_blob",
    "write_event",
    "write_row",
    "list_events",
    "status",
)
_MODULE_OPS_REQUIRED = (
    "contract_version",
    "module_id",
    "module_application_id",
    "detect",
    "verify",
    "backup",
    "restore",
    "migrate",
    "export",
    "rehydrate",
)

_VERSION_REQUIREMENT_RE = re.compile(r"^\s*(>=|<=|==|>|<)?\s*([0-9]+(?:\.[0-9]+)*)\s*$")


def _parse_version(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for token in str(version or "").strip().split("."):
        if not token:
            continue
        if not token.isdigit():
            break
        parts.append(int(token))
    return tuple(parts)


def _compare_versions(left: tuple[int, ...], right: tuple[int, ...]) -> int:
    width = max(len(left), len(right))
    left_padded = left + (0,) * (width - len(left))
    right_padded = right + (0,) * (width - len(right))
    if left_padded < right_padded:
        return -1
    if left_padded > right_padded:
        return 1
    return 0


def ensure_interface_compatibility(obj: Any, *, interface: str) -> None:
    """Fail fast with deterministic errors when a storage implementation drifts."""

    normalized = str(interface or "").strip().lower()
    if normalized == "structured_store":
        required = tuple(
            item for item in _STRUCTURED_REQUIRED if item != "check_capability"
        )  # Exclude v1.1 optional method
    elif normalized == "vector_store":
        required = tuple(
            item for item in _VECTOR_REQUIRED if item != "check_capability"
        )  # Exclude v1.1 optional method
    elif normalized == "record_store":
        required = _RECORD_REQUIRED
    elif normalized == "blob_store":
        required = _BLOB_REQUIRED
    elif normalized == "hybrid_store":
        required = _HYBRID_REQUIRED
    elif normalized == "module_ops":
        required = _MODULE_OPS_REQUIRED
    else:
        raise ValueError(f"unknown interface: {interface}")

    missing: list[str] = []
    for name in required:
        if not hasattr(obj, name):
            missing.append(name)
            continue
        value = getattr(obj, name)
        # Special exception for attributes that are not callable
        _ATTRIBUTE_MEMBERS = {
            "contract_version",
            "describe_backend",
            "module_id",
            "module_application_id",
        }
        if name not in _ATTRIBUTE_MEMBERS and not callable(value):
            missing.append(name)

    if missing:
        raise TypeError(
            f"{obj.__class__.__name__} does not satisfy {normalized} interface; missing required members: {', '.join(missing)}"
        )

    version = str(getattr(obj, "contract_version", "")).strip()
    if version != STORAGE_INTERFACE_VERSION and normalized not in (
        "structured_store",
        "vector_store",
        "module_ops",
    ):
        raise TypeError(
            f"{obj.__class__.__name__} has unsupported contract_version={version!r}; expected {STORAGE_INTERFACE_VERSION!r}"
        )


def create_capability_error_envelope(
    operation: str,
    requirement: CapabilityRequirement,
    descriptor: BackendDescriptor,
    message: str,
) -> StorageEnvelope:
    """
    Helper to create a StorageEnvelope for capability mismatch errors.
    """
    capability_error = UnsupportedCapabilityError(
        requirement=requirement, descriptor=descriptor, message=message
    )

    return StorageEnvelope(
        operation=operation,
        ok=False,
        error=capability_error,
        contract_version=STORAGE_INTERFACE_VERSION,
    )


def check_capability_support(
    backend_descriptor: BackendDescriptor, requirement: CapabilityRequirement
) -> bool:
    """
    Determine if a backend supports a specific capability requirement.
    This is a basic check that considers version range and required features.
    """
    requirement_range = str(requirement.version_range or "").strip()
    if requirement_range:
        match = _VERSION_REQUIREMENT_RE.match(requirement_range)
        if not match:
            return False
        operator = match.group(1) or "=="
        required_version = _parse_version(match.group(2))
        backend_version = _parse_version(backend_descriptor.version)
        if not required_version or not backend_version:
            return False
        comparison = _compare_versions(backend_version, required_version)
        if operator == ">=" and comparison < 0:
            return False
        if operator == ">" and comparison <= 0:
            return False
        if operator == "<=" and comparison > 0:
            return False
        if operator == "<" and comparison >= 0:
            return False
        if operator == "==" and comparison != 0:
            return False

    # Check if all required specific features are present in backend capabilities
    for req_feature in requirement.required_features:
        # Handle plane support checks specially
        if req_feature in ["record", "blob", "vector"]:
            if req_feature not in backend_descriptor.planes_supported:
                return False
        elif req_feature not in backend_descriptor.capabilities:
            return False
        elif backend_descriptor.capabilities.get(req_feature) is False:
            return False

    return True
