from dataclasses import dataclass, field
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing helpers only
    from .registry.catalog import ToolSpec


@dataclass
class ToolContext:
    """Execution context passed to plugins."""

    session_id: str | None
    trace_id: str | None
    agent_id: str | None
    workspace_root: str
    run_id: str
    policy_client: Any
    artifact_client: Any
    safety_client: Any
    env: dict[str, str] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolPlan:
    summary: str
    requires_confirm: bool = False
    estimated_risk: str = "low"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None


@dataclass
class SafetyDecision:
    allowed: bool
    reason: str
    code: str = "OK"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str
    code: str = "OK"
    requires_confirm: bool = False
    modified_args: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ToolPlugin(Protocol):
    """Generic tool plugin contract for openminion-tool plugin packages."""

    tool_id: str
    capabilities: tuple[str, ...]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]

    def invoke(self, ctx: ToolContext, input_data: dict[str, Any]) -> ToolResult: ...


@runtime_checkable
class SupportsDryRun(Protocol):
    def dry_run(self, input_data: dict[str, Any]) -> ToolPlan: ...


@runtime_checkable
class SupportsCancel(Protocol):
    def cancel(self, handle: str) -> bool: ...


@runtime_checkable
class SafetyAdapter(Protocol):
    def evaluate(self, *, tool: str, args: dict[str, Any]) -> SafetyDecision: ...


@runtime_checkable
class PolicyAdapter(Protocol):
    def evaluate(
        self, *, tool_name: str, tool_spec: "ToolSpec", args: dict[str, Any]
    ) -> PolicyDecision: ...
