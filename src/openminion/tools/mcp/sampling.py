"""OpenMinion-backed MCP sampling bridge."""

import inspect
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from openminion.modules.llm.providers.base import (
    ProviderHistoryMessage,
    ProviderRequest,
    ProviderResponse,
)
from openminion.modules.llm.runtime.sync import run_async_compat

from .schemas import MCPSamplingRequest, MCPSamplingResult

SamplingExecutor = Callable[[ProviderRequest], ProviderResponse | Awaitable[Any]]


@dataclass(frozen=True)
class MCPSamplingAuditEvent:
    server_name: str
    mode: str
    allowed: bool
    model: str = ""
    stop_reason: str = ""
    message_count: int = 0
    timestamp: float = 0.0


class OpenMinionSamplingHandler:
    """MCP sampling handler backed by an OpenMinion provider executor."""

    def __init__(self, *, mode: str = "deny") -> None:
        self._mode = str(mode or "deny").strip().lower()
        self._executor: SamplingExecutor | None = None
        self._events: list[MCPSamplingAuditEvent] = []

    def bind_executor(self, executor: SamplingExecutor) -> None:
        self._executor = executor

    def events(self) -> list[MCPSamplingAuditEvent]:
        return list(self._events)

    def sample(
        self,
        *,
        server_name: str,
        request: MCPSamplingRequest,
    ) -> MCPSamplingResult:
        if self._mode != "allow" or self._executor is None:
            result = MCPSamplingResult(
                role="assistant",
                content={
                    "type": "text",
                    "text": "Sampling denied by OpenMinion MCP sampling policy.",
                },
                model="openminion-policy",
                stop_reason="denied",
            )
            self._record(
                server_name=server_name,
                request=request,
                allowed=False,
                result=result,
            )
            return result

        provider_request = _provider_request_from_sampling(
            server_name=server_name,
            request=request,
        )
        raw_response = self._executor(provider_request)
        if inspect.isawaitable(raw_response):
            raw_response = run_async_compat(raw_response)
        response = _coerce_provider_response(raw_response)
        result = MCPSamplingResult(
            role="assistant",
            content={"type": "text", "text": response.text},
            model=response.model,
            stop_reason=response.finish_reason or "endTurn",
        )
        self._record(
            server_name=server_name,
            request=request,
            allowed=True,
            result=result,
        )
        return result

    def _record(
        self,
        *,
        server_name: str,
        request: MCPSamplingRequest,
        allowed: bool,
        result: MCPSamplingResult,
    ) -> None:
        self._events.append(
            MCPSamplingAuditEvent(
                server_name=server_name,
                mode=self._mode,
                allowed=bool(allowed),
                model=str(result.model or "").strip(),
                stop_reason=str(result.stop_reason or "").strip(),
                message_count=len(request.messages),
                timestamp=time.time(),
            )
        )


def sampling_handler_from_runtime_config(
    runtime_config: Any,
) -> OpenMinionSamplingHandler | None:
    mode = str(getattr(runtime_config, "mcp_sampling_mode", "disabled") or "disabled")
    mode = mode.strip().lower() or "disabled"
    if mode == "disabled":
        return None
    return OpenMinionSamplingHandler(mode=mode)


def _provider_request_from_sampling(
    *,
    server_name: str,
    request: MCPSamplingRequest,
) -> ProviderRequest:
    messages = list(request.messages)
    system_prompt = request.system_prompt
    history: list[ProviderHistoryMessage] = []
    user_message = ""
    for message in messages:
        role = str(message.role or "").strip().lower() or "user"
        text = _content_to_text(message.content)
        if role == "system" and not system_prompt:
            system_prompt = text
            continue
        if role == "user":
            user_message = text
        else:
            history.append(ProviderHistoryMessage(role=role, content=text))
    if not user_message and messages:
        user_message = _content_to_text(messages[-1].content)
    return ProviderRequest(
        user_message=user_message,
        system_prompt=system_prompt,
        thinking="minimal",
        history=history,
        tools=[],
        metadata={
            "origin": "mcp.sampling",
            "mcp_server": server_name,
            "mcp_max_tokens": str(request.max_tokens or ""),
        },
    )


def _coerce_provider_response(value: Any) -> ProviderResponse:
    if isinstance(value, ProviderResponse):
        return value
    return ProviderResponse(
        text=str(getattr(value, "text", "") or value or ""),
        model=str(getattr(value, "model", "") or ""),
        usage=dict(getattr(value, "usage", {}) or {}),
        tool_calls=list(getattr(value, "tool_calls", []) or []),
        finish_reason=str(getattr(value, "finish_reason", "") or ""),
    )


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if str(content.get("type", "") or "").strip().lower() == "text":
            return str(content.get("text", "") or "")
        return " ".join(
            str(value) for value in content.values() if isinstance(value, str)
        ).strip()
    if isinstance(content, list):
        return "\n".join(_content_to_text(item) for item in content).strip()
    return str(content or "")


__all__ = [
    "MCPSamplingAuditEvent",
    "OpenMinionSamplingHandler",
    "sampling_handler_from_runtime_config",
]
