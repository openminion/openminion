from collections.abc import Callable
from dataclasses import dataclass
from threading import Event
from typing import Any

from .errors import (
    A2AError,
    ERROR_CODE_AGENT_NOT_FOUND,
    ERROR_CODE_NO_HANDLER,
    ERROR_CODE_ROUTE_NOT_FOUND,
)
from .models import AgentDescriptor, Envelope
from .storage.base import StateStore

AgentHandler = Callable[[Envelope], dict[str, Any]]
JobHandler = Callable[[Envelope, Event], dict[str, Any]]


@dataclass
class ResolvedRoute:
    descriptor: AgentDescriptor
    handler: AgentHandler
    job_handler: JobHandler | None = None


class AgentRegistry:
    def __init__(self, state_store: StateStore) -> None:
        self._state_store = state_store
        self._handlers: dict[str, AgentHandler] = {}
        self._job_handlers: dict[str, JobHandler] = {}

    def register(
        self,
        descriptor: AgentDescriptor,
        handler: AgentHandler,
        *,
        job_handler: JobHandler | None = None,
    ) -> None:
        self._handlers[descriptor.agent_id] = handler
        if job_handler is not None:
            self._job_handlers[descriptor.agent_id] = job_handler
        else:
            self._job_handlers.pop(descriptor.agent_id, None)
        self._state_store.upsert_agent(descriptor)

    def list_agents(self) -> list[AgentDescriptor]:
        return self._state_store.list_agents()

    def resolve(self, envelope: Envelope) -> ResolvedRoute:
        agents = self._state_store.list_agents()

        if envelope.to_agent:
            return self._resolve_target_agent(agents, envelope.to_agent)

        method = envelope.method
        for descriptor in agents:
            if descriptor.supports_method(method):
                handler = self._handlers.get(descriptor.agent_id)
                if handler is not None:
                    return ResolvedRoute(
                        descriptor=descriptor,
                        handler=handler,
                        job_handler=self._job_handlers.get(descriptor.agent_id),
                    )

        raise A2AError(
            ERROR_CODE_ROUTE_NOT_FOUND,
            f"No route found for method '{method}'",
            {"method": method, "to_capability": envelope.to_capability},
        )

    def _resolve_target_agent(
        self, agents: list[AgentDescriptor], target: str
    ) -> ResolvedRoute:
        for descriptor in agents:
            if descriptor.agent_id != target:
                continue
            handler = self._handlers.get(target)
            if handler is None:
                raise A2AError(
                    ERROR_CODE_NO_HANDLER,
                    f"No local handler for target agent '{target}'",
                )
            return ResolvedRoute(
                descriptor=descriptor,
                handler=handler,
                job_handler=self._job_handlers.get(target),
            )
        raise A2AError(ERROR_CODE_AGENT_NOT_FOUND, f"Target agent '{target}' not found")
