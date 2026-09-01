import hashlib
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..errors import ToolRuntimeError


@dataclass(frozen=True, slots=True)
class MemoryAccessContext:
    """Runtime-owned identity available to model-facing memory tools."""

    agent_id: str
    session_id: str
    capture_id: str = ""
    tool_call_id: str = ""

    def require_scope(self, scope: str) -> str:
        normalized = str(scope or "").strip()
        allowed = {
            candidate
            for candidate in (
                f"agent:{self.agent_id}" if self.agent_id else "",
                f"session:{self.session_id}" if self.session_id else "",
            )
            if candidate
        }
        if normalized not in allowed:
            raise ToolRuntimeError(
                "POLICY_DENIED",
                "memory tools may access only the active agent or session scope",
                {
                    "reason_code": "memory_scope_not_permitted",
                    "requested_scope": normalized,
                },
            )
        return normalized

    def require_scopes(self, scopes: list[str]) -> list[str]:
        return [self.require_scope(scope) for scope in scopes]

    def explicit_operation_id(self, operation: str) -> str:
        if not self.capture_id:
            return ""
        if not self.tool_call_id:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                "capture-bound memory operations require a canonical tool call ID",
                {"reason_code": "memory_tool_call_id_missing"},
            )
        payload = "|".join(
            (
                "memory-tool.v1",
                str(operation or "").strip(),
                self.capture_id,
                self.tool_call_id,
            )
        )
        return f"memory-tool-{hashlib.sha256(payload.encode()).hexdigest()}"


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
