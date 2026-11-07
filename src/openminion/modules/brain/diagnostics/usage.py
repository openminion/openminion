"""Bind LLM response facts to the active runner step's durable event stream."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import json
from typing import Any
from uuid import uuid4

from openminion.modules.brain.interfaces import SessionAPI
from openminion.modules.context.compress.token_count import count_tokens
from openminion.modules.llm.schemas import LLMResponse
from openminion.modules.llm.client_call import (
    response_cost_payload,
    usage_payload_from_response_usage,
)

_CALL_CONTEXT: ContextVar[tuple[SessionAPI, str, str, str] | None] = ContextVar(
    "brain_call_context", default=None
)


@contextmanager
def llm_usage_scope(
    session_api: SessionAPI, session_id: str, trace_id: str, run_id: str
) -> Iterator[None]:
    token = _CALL_CONTEXT.set((session_api, session_id, trace_id, run_id))
    try:
        yield
    finally:
        _CALL_CONTEXT.reset(token)


# Attribution sources are named so a reader can tell a measured cell from an absent
# one: `serialized_request` values come from the outbound request this process built,
# while `usage_only` means the caller did not hand this owner the request, so the
# context columns are unknown rather than zero. Provider-billed totals stay in
# `usage` and are never inferred from the estimate.
ATTRIBUTION_SERIALIZED = "serialized_request"
ATTRIBUTION_USAGE_ONLY = "usage_only"
CONTEXT_TOKENS_SOURCE = "estimate:count_tokens"


def _payload(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _serialized_size(payload: Any) -> int:
    return len(json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8"))


def request_attribution(
    *,
    messages: list[Any] | None,
    tools: list[Any] | None,
    response: LLMResponse,
) -> dict[str, Any]:
    """Request-side attribution for a call this owner did not itself assemble.

    Entry carries the context pack's own estimate. Every other purpose reaches here
    through the provider adapter, so the only request facts available are the
    serialized messages and tool specs that were actually sent; those are measured,
    and the estimate is named as an estimate. A column whose input the caller did not
    supply is omitted instead of reported as zero, and `attribution` says which kind
    of call this was, so a consumer cannot read an absent cell as an empty one.
    """
    if messages is None and tools is None:
        return {"attribution": ATTRIBUTION_USAGE_ONLY}
    message_payload = [_payload(message) for message in (messages or [])]
    tool_payload = [_payload(tool) for tool in (tools or [])]
    segment_ids = {
        str(segment_id)
        for message in (messages or [])
        for part in list(getattr(message, "content_parts", []) or [])
        for segment_id in list(getattr(part, "segment_ids", []) or [])
        if str(segment_id).strip()
    }
    outbound: dict[str, Any] = {
        "attribution": ATTRIBUTION_SERIALIZED,
        "context_bytes": _serialized_size(message_payload),
        "context_tokens": max(
            0, int(count_tokens(json.dumps(message_payload, default=str)))
        ),
        "context_tokens_source": CONTEXT_TOKENS_SOURCE,
        "context_segment_count": len(segment_ids),
        "response_bytes": _serialized_size(
            {
                "output_text": str(getattr(response, "output_text", "") or ""),
                "tool_calls": [
                    _payload(call)
                    for call in list(getattr(response, "tool_calls", []) or [])
                ],
            }
        ),
    }
    if tools is not None:
        outbound["tool_schema_count"] = len(tool_payload)
        outbound["tool_schema_bytes"] = _serialized_size(tool_payload)
        outbound["request_bytes"] = _serialized_size(
            {"messages": message_payload, "tools": tool_payload}
        )
    else:
        outbound["request_bytes"] = _serialized_size({"messages": message_payload})
    return outbound


def record_llm_response(
    *,
    purpose: str,
    response: LLMResponse,
    messages: list[Any] | None = None,
    tools: list[Any] | None = None,
) -> None:
    context = _CALL_CONTEXT.get()
    # Entry owns its completion event, including manifest and routing metrics.
    if context is None or purpose == "entry":
        return
    session_api, session_id, trace_id, run_id = context
    ok = bool(response.ok)
    session_api.emit_canonical_event(
        session_id,
        "llm.call.completed" if ok else "llm.call.failed",
        {
            "llm_call_id": str(uuid4()),
            "purpose": purpose,
            "run_id": run_id,
            "provider": response.provider,
            "model": response.model,
            "usage": usage_payload_from_response_usage(response.usage),
            **response_cost_payload(response),
            **request_attribution(messages=messages, tools=tools, response=response),
        },
        trace_id=trace_id,
    )
