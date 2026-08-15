import logging
from typing import Any
from collections.abc import Callable

from openminion.base.channel import ChannelRegistry
from openminion.modules.storage.runtime.session_store import (
    RuntimeSessionTurnFenceError,
    SessionStore,
)
from openminion.services.agent import AgentService
from openminion.services.agent.telemetry import InvocationLifecycleFact
from openminion.services.context.session import SessionContextService
from openminion.services.gateway.memory import MemoryFollowupQueue
from openminion.services.gateway.security import GatewaySecurity
from openminion.services.gateway.turn.lifecycle import _GatewayTurnLifecycleOps

from .flow_agent import GatewayTurnAgentExecutionMixin
from .flow_models import _response_is_pae_idle_tick_noop
from .flow_persistence import GatewayTurnPersistenceDeliveryMixin
from .flow_routing import GatewayTurnRoutingMixin
from .flow_setup import GatewayTurnSetupMixin

__all__ = ["GatewayTurnRunnerFlowMixin", "_response_is_pae_idle_tick_noop"]


class GatewayTurnRunnerFlowMixin(
    GatewayTurnRoutingMixin,
    GatewayTurnSetupMixin,
    GatewayTurnAgentExecutionMixin,
    GatewayTurnPersistenceDeliveryMixin,
):
    def __init__(
        self,
        *,
        agent: AgentService,
        agent_memory: Any,
        channels: ChannelRegistry,
        logger: logging.Logger,
        sessions: SessionStore,
        session_context: SessionContextService,
        security: GatewaySecurity,
        agent_id: str,
        history_limit: int,
        memory_capsule_strategy: str,
        memory_capsule_cache: dict[str, str],
        memory_dynamic_retrieval_enabled: bool,
        emit_run_state: Callable[..., Any],
        knowledge_graphs: Any | None = None,
        typed_terminal_resolver: Callable[..., tuple[Any, ...] | None] | None = None,
        emit_invocation_lifecycle: Callable[[InvocationLifecycleFact], bool]
        | None = None,
    ) -> None:
        self._agent = agent
        self._agent_memory = agent_memory
        self._knowledge_graphs = knowledge_graphs
        self._channels = channels
        self._logger = logger
        self._sessions = sessions
        self._session_context = session_context
        self._security = security
        self._agent_id = agent_id
        self._history_limit = history_limit
        self._memory_capsule_strategy = memory_capsule_strategy
        self._memory_capsule_cache = memory_capsule_cache
        self._memory_followup_queue = MemoryFollowupQueue(auto_start=False)
        self._emit_memory_followup = getattr(
            agent,
            "emit_memory_followup_sync",
            None,
        )
        self._memory_dynamic_retrieval_enabled = memory_dynamic_retrieval_enabled
        self._emit_run_state = emit_run_state
        self._typed_terminal_resolver = typed_terminal_resolver
        self._lifecycle_ops = _GatewayTurnLifecycleOps(
            sessions=sessions,
            logger=logger,
            emit_run_state=emit_run_state,
            emit_invocation_lifecycle=emit_invocation_lifecycle,
            typed_terminal_resolver=typed_terminal_resolver,
        )

    def _renew_session_turn_lease(
        self,
        *,
        session_id: str,
        owner: str,
        fence_token: int | None,
    ) -> None:
        if fence_token is None:
            return
        renew = getattr(self._sessions, "renew_session_turn_lease", None)
        if callable(renew) and renew(
            session_id,
            owner=owner,
            fence_token=fence_token,
            ttl_s=60,
        ):
            return
        raise RuntimeSessionTurnFenceError("session turn lease renewal failed")

    def flush_memory_followups(self, *, session_id: str | None = None) -> None:
        self._memory_followup_queue.flush(session_id=session_id)

    def _emit_terminal_run_state(
        self,
        *,
        session_id: str,
        run_id: str,
        legacy_state: str,
        current_step: str,
        payload: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        thread_id: str | None = None,
        attach_id: str | None = None,
        typed_terminal_resolver: Callable[..., tuple[Any, ...] | None] | None = None,
    ) -> Any:
        return self._lifecycle_ops.emit_terminal_run_state(
            session_id=session_id,
            run_id=run_id,
            legacy_state=legacy_state,
            current_step=current_step,
            payload=payload,
            conversation_id=conversation_id,
            thread_id=thread_id,
            attach_id=attach_id,
            typed_terminal_resolver=typed_terminal_resolver,
        )
