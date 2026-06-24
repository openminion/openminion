import json
from dataclasses import replace
from typing import Any

from openminion.base.types import AgentResponse, Message
from openminion.services.agent.hooks import HookContext
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionResult

from ..telemetry import merge_metadata
from .state import TurnRuntimeContext


def tool_calls_payload(tool_calls: list[ProviderToolCall]) -> str:
    return json.dumps(
        [
            {
                "id": str(getattr(call, "id", "") or ""),
                "name": str(getattr(call, "name", "") or ""),
                "arguments": getattr(call, "arguments", {}) or {},
                "source": str(getattr(call, "source", "") or ""),
                "depends_on": [
                    str(dep).strip()
                    for dep in (
                        list(getattr(call, "depends_on", []) or [])
                        if isinstance(
                            getattr(call, "depends_on", []), (list, tuple, set)
                        )
                        else [getattr(call, "depends_on")]
                        if isinstance(getattr(call, "depends_on", None), str)
                        else []
                    )
                    if str(dep).strip()
                ],
            }
            for call in tool_calls
        ],
        sort_keys=True,
    )


def format_tool_results(results: list[ToolExecutionResult]) -> str:
    outputs = []
    for res in results:
        if res.ok:
            outputs.append(res.content or str(res.data))
        else:
            outputs.append(f"Error: {res.error}")
    return "\n".join(outputs).strip() or "Tool executed."


def merge_turn_metadata(
    service: Any,
    runtime: TurnRuntimeContext,
    metadata: dict[str, str],
    *,
    model: str | None = None,
) -> dict[str, str]:
    return merge_metadata(
        metadata,
        model=model,
        provider_name=str(getattr(service._provider, "name", "") or ""),
        inference_steps=runtime.inference_steps,
        untrusted_metadata=runtime.untrusted_metadata,
        untrusted_events=runtime.untrusted_events,
        self_improvement_metadata=runtime.self_improvement_metadata,
    )


def finalize_turn_response(
    service: Any,
    runtime: TurnRuntimeContext,
    response: AgentResponse,
    *,
    inbound: Message,
    plugin_context: HookContext,
) -> AgentResponse:
    response = replace(
        response,
        metadata=merge_turn_metadata(
            service,
            runtime,
            response.metadata,
            model=str(
                getattr(response, "model", "") or response.metadata.get("model", "")
            ),
        ),
    )
    return service._plugins.apply_outbound(response, inbound, plugin_context)
