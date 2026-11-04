"""Bind LLM response facts to the active runner step's durable event stream."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from openminion.modules.brain.interfaces import SessionAPI
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


def record_llm_response(*, purpose: str, response: LLMResponse) -> None:
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
        },
        trace_id=trace_id,
    )
