from __future__ import annotations

from typing import Any

from openminion.modules.brain.adapters.context.runtime import ContextCtlAdapter


class _Pack:
    def model_dump(self) -> dict[str, Any]:
        return {"messages": [], "segments": []}


class _Service:
    def __init__(self) -> None:
        self.request = None

    def build_pack(self, request):
        self.request = request
        return _Pack()


def test_context_adapter_passes_self_awareness_payload_to_context_request() -> None:
    service = _Service()
    adapter = ContextCtlAdapter(service)
    self_awareness = {
        "schema_version": "self_model.v1",
        "health": "ok",
        "agent_id": "mini",
    }

    adapter.build(
        session_id="s1",
        agent_id="mini",
        purpose="decide",
        budget={},
        hints={"query": "what are you?", "self_awareness": self_awareness},
    )

    assert service.request is not None
    assert service.request.self_awareness == self_awareness
