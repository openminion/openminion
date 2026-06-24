from dataclasses import dataclass, field
from typing import Any, Mapping

from openminion.modules.llm.providers.base import ProviderRequest, ProviderResponse
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolExecutionBatch

from ..state import RequiredLaneOutcome


@dataclass(slots=True)
class _PhaseResult:
    action: str = "advance"
    next_tool: str | None = None
    outcome: RequiredLaneOutcome | None = None
    state_updates: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RequiredLaneState:
    all_attempts: list[ToolExecutionBatch] = field(default_factory=list)
    tool_to_try: str | None = None
    current_fallback_idx: int = 0
    arg_retry_attempted: bool = False
    denied_tool_recovery_attempted: bool = False
    required_tool_retry_attempted: bool = False
    attempted_tools: list[str] = field(default_factory=list)
    termination_reason: str | None = None
    capability_fallback_trigger_reason: str | None = None
    spec: Any | None = None
    request: ProviderRequest | None = None
    response: ProviderResponse | None = None
    runtime_args_filled: bool = False
    ctx: ToolExecutionContext | None = None
    batch: ToolExecutionBatch | None = None
    security_events: list[dict[str, str]] = field(default_factory=list)

    def apply_updates(self, updates: Mapping[str, Any]) -> None:
        for key, value in updates.items():
            setattr(self, key, value)


__all__ = ["RequiredLaneState", "_PhaseResult"]
