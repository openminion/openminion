from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MemoryToolRuntimeService(Protocol):
    """Memory service seam available to tool runtime handlers."""

    def write_record(
        self,
        *,
        scope: str,
        record_type: str,
        title: str,
        content: dict[str, Any] | str,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        confidence: float | None = None,
    ) -> str: ...

    def search(self, options: Any) -> list[Any]: ...

    def delete_record(self, record_id: str, *, reason: str | None = None) -> bool: ...
