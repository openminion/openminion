from __future__ import annotations

from typing import Any, ClassVar, Protocol, runtime_checkable

from sophiagraph.query import ListQueryOptions, SearchQueryOptions

from .contracts import (
    MEMORY_CONTRACT_VERSION,
    ensure_memory_contract_compatibility,
)
from .models import MemoryRecord


MEMORY_INTERFACE_VERSION = MEMORY_CONTRACT_VERSION


class MemoryServiceInterfaceError(Exception):
    def __init__(self, message: str) -> None:
        self.code = "MEMORY_SERVICE_INTERFACE_VIOLATION"
        self.message = message
        super().__init__(message)


def _render_compatibility_error(service: Any, item: Any) -> str:
    text = str(item or "")
    for prefix in ("missing member: ", "non-callable member: "):
        if text.startswith(prefix):
            return "Missing required method: " + text.split(prefix, 1)[1]
    if text.startswith("version mismatch:"):
        declared = getattr(service, "contract_version", "")
        return f"Version mismatch: expected {MEMORY_INTERFACE_VERSION}, got {declared}"
    return text


class MemoryServiceInterface(Protocol):
    """Memory service interface contract."""

    contract_version: ClassVar[str] = MEMORY_INTERFACE_VERSION

    def __init__(
        self, store: Any, policy: Any | None = None, vector_adapter: Any = None
    ): ...

    def set_vector_adapter(self, vector_adapter: Any) -> None: ...

    def get(self, record_id: str) -> Any: ...

    def list(self, options: Any) -> list[Any]: ...

    def search(self, options: Any) -> list[Any]: ...

    def search_semantic(
        self,
        query: str,
        scopes: list[str],
        *,
        types: list[Any] | None = None,
        limit: int | None = None,
    ) -> list[Any]: ...

    def candidate_put(self, candidate: Any) -> str: ...

    def candidate_get(self, candidate_id: str) -> Any: ...

    def candidate_list(self, options: Any) -> list[Any]: ...

    def candidate_update(self, candidate_id: str, patch: dict[str, Any]) -> Any: ...

    def promote_candidate(self, candidate_id: str, target_scope: str) -> Any: ...


@runtime_checkable
class MemoryNamespaceQueryInterface(Protocol):
    """Runtime-facing durable memory query contract."""

    def list_records(self, options: ListQueryOptions) -> list[MemoryRecord]: ...

    def search_records(self, options: SearchQueryOptions) -> list[MemoryRecord]: ...


def ensure_memory_compatibility(
    service: Any, strict: bool = True
) -> tuple[bool, list[str]]:
    valid, raw_errors = ensure_memory_contract_compatibility(
        service,
        role="service",
        strict=False,
    )
    if valid:
        return True, []
    errors = [_render_compatibility_error(service, item) for item in raw_errors]
    errors.sort(key=lambda item: 0 if str(item).startswith("Version mismatch") else 1)

    if errors:
        if strict:
            raise MemoryServiceInterfaceError(f"Memory service incompatible: {errors}")
        return False, errors

    return True, []
