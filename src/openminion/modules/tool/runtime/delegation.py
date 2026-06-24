from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class A2ADelegateResult:
    ok: bool
    status: str
    content: str = ""
    error_code: str = ""
    error_message: str = ""
    target_agent_id: str = ""
    trace_id: str = ""
    task_id: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class A2ADelegateApi(Protocol):
    """A2A delegation seam available to tool runtime handlers."""

    def delegate(
        self,
        *,
        agent_id: str,
        instruction: str,
        timeout_seconds: int,
    ) -> A2ADelegateResult: ...


__all__ = ["A2ADelegateApi", "A2ADelegateResult"]
