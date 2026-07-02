"""Public API re-exports for ``openminion.api``."""

from openminion.api.agent import (
    Agent,
    AgentOutputValidationError,
    AgentRunResult,
)
from openminion.api.handoff import Handoff, subagent
from openminion.api.runtime import APIRuntime


def __getattr__(name: str):
    if name == "dispatch_request":
        from openminion.api.server import dispatch_request

        return dispatch_request
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "APIRuntime",
    "Agent",
    "AgentOutputValidationError",
    "AgentRunResult",
    "Handoff",
    "dispatch_request",
    "subagent",
]
