from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from openminion.base.types import Message
from openminion.services.agent.hooks import HookContext
from openminion.modules.llm.providers.base import ProviderHistoryMessage


@dataclass(slots=True)
class TurnRuntimeContext:
    inbound: Message
    plugin_context: HookContext
    system_prompt: str
    provider_history: list[ProviderHistoryMessage]
    user_message: str
    untrusted_metadata: dict[str, Any]
    untrusted_events: list[dict[str, Any]]
    progress_callback: Callable[[dict[str, Any]], None] | None = None
    approval_callback: Callable[[str, dict[str, Any], Any], Awaitable[bool]] | None = (
        None
    )
    self_improvement_metadata: dict[str, str] = field(default_factory=dict)
    inference_steps: int = 0


@dataclass(slots=True)
class ToolPlan:
    user_message: str
    intent_category: str
    effective_forced_tools: list[str]
    fallback_chain: list[str]
    capability_primary: str | None
    unavailable_reason: str | None
    requested_forced_tools: list[str]


@dataclass(slots=True)
class RequiredLaneOutcome:
    response: Any
    attempted_tools: list[str]
    capability_fallback_trigger_reason: str | None
