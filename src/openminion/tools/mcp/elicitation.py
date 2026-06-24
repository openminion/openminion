"""OpenMinion MCP elicitation bridge."""

import time
from dataclasses import dataclass
from typing import Any, Callable

from .schemas import MCPElicitationRequest, MCPElicitationResult

ElicitationResponder = Callable[
    [MCPElicitationRequest], MCPElicitationResult | dict[str, Any]
]


@dataclass(frozen=True)
class MCPElicitationAuditEvent:
    server_name: str
    mode: str
    request_mode: str
    action: str
    elicitation_id: str = ""
    timestamp: float = 0.0


class OpenMinionElicitationHandler:
    """Typed bridge for user-visible MCP elicitation requests."""

    def __init__(self, *, mode: str = "decline", url_supported: bool = False) -> None:
        self._mode = str(mode or "decline").strip().lower()
        self._url_supported = bool(url_supported)
        self._responder: ElicitationResponder | None = None
        self._events: list[MCPElicitationAuditEvent] = []

    def bind_responder(self, responder: ElicitationResponder) -> None:
        self._responder = responder
        self._mode = "interactive"

    def set_url_supported(self, value: bool) -> None:
        self._url_supported = bool(value)

    def events(self) -> list[MCPElicitationAuditEvent]:
        return list(self._events)

    def elicit(
        self,
        *,
        server_name: str,
        request: MCPElicitationRequest,
    ) -> MCPElicitationResult:
        request_mode = str(request.mode or "").strip().lower() or "form"
        if request_mode == "url" and not self._url_supported:
            result = MCPElicitationResult(
                action="decline",
                content={"reason": "url_unsupported"},
            )
            self._record(server_name=server_name, request=request, result=result)
            return result
        if self._mode != "interactive" or self._responder is None:
            result = MCPElicitationResult(action="decline")
            self._record(server_name=server_name, request=request, result=result)
            return result

        result = _coerce_elicitation_result(self._responder(request))
        self._record(server_name=server_name, request=request, result=result)
        return result

    def _record(
        self,
        *,
        server_name: str,
        request: MCPElicitationRequest,
        result: MCPElicitationResult,
    ) -> None:
        self._events.append(
            MCPElicitationAuditEvent(
                server_name=server_name,
                mode=self._mode,
                request_mode=str(request.mode or "").strip(),
                action=str(result.action or "").strip(),
                elicitation_id=str(request.elicitation_id or "").strip(),
                timestamp=time.time(),
            )
        )


def _coerce_elicitation_result(
    value: MCPElicitationResult | dict[str, Any],
) -> MCPElicitationResult:
    if isinstance(value, MCPElicitationResult):
        return value
    if isinstance(value, dict):
        content = value.get("content")
        return MCPElicitationResult(
            action=str(value.get("action", "") or "decline").strip() or "decline",
            content=dict(content) if isinstance(content, dict) else None,
        )
    return MCPElicitationResult(action="decline")


__all__ = [
    "ElicitationResponder",
    "MCPElicitationAuditEvent",
    "OpenMinionElicitationHandler",
]
