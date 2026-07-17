from dataclasses import dataclass, field
from typing import Any

from openminion.base.types import AgentResponse, Message
from openminion.services.constants import MEMORY_CAPSULE_STRATEGY_OFF
from openminion.modules.controlplane.channels.authenticity import ChannelAuthenticityDecision
from openminion.modules.task.run import ThreadLifecycleProjection


@dataclass(frozen=True)
class RoutingState:
    session: Any
    normalized_request_id: str
    normalized_inbound_metadata: dict[str, str]
    conversation_id: str
    thread_id: str
    attach_id: str
    lifecycle: ThreadLifecycleProjection
    routing_action: str
    routing_reason: str
    lifecycle_payload: dict[str, str]
    run_id: str


@dataclass
class TurnContext:
    history: list[Message]
    prior_transcript_available: bool
    memory_context: str = ""
    memory_retrieval_context: str = ""
    knowledge_graph_context: str = ""
    memory_context_meta: dict[str, str] = field(default_factory=dict)
    memory_retrieval_meta: dict[str, str] = field(default_factory=dict)
    knowledge_graph_meta: dict[str, str] = field(default_factory=dict)
    memory_strategy: str = MEMORY_CAPSULE_STRATEGY_OFF
    capsule_cache_hit: bool = False


@dataclass(frozen=True)
class TurnExecution:
    response: AgentResponse
    outbound: Message
    authenticity_decision: ChannelAuthenticityDecision
