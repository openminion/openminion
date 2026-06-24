from __future__ import annotations

from unittest.mock import Mock

from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _make_adapter(*, retrieve_ctl: object | None = None) -> MemoryServiceGatewayAdapter:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    return MemoryServiceGatewayAdapter(
        service,
        agent_id="rmq-c-agent",
        retrieve_ctl=retrieve_ctl,
    )


def _pipeline(adapter: MemoryServiceGatewayAdapter):
    return adapter._pipeline  # noqa: SLF001


def test_retrieve_split_uses_direct_query_for_both_lanes() -> None:
    retrieve_ctl = Mock(name="retrieve_ctl")
    retrieve_ctl.retrieve.side_effect = [
        [{"text": "todo buy milk", "score": 0.8, "meta": {"unit_id": "u1"}}],
        [{"text": "runbook", "score": 0.7, "meta": {"unit_id": "u2"}}],
    ]
    adapter = _make_adapter(retrieve_ctl=retrieve_ctl)

    merged, counts = _pipeline(adapter)._retrieve_split(  # noqa: SLF001
        retrieve_ctl,
        query="what task should I do next?",
        session_id="s",
        agent_id="a",
        project_id=None,
        k_conversational=3,
        k_knowledge=2,
    )

    assert counts == {"conversational": 1, "knowledge": 1}
    assert [item["meta"]["unit_id"] for item in merged] == ["u1", "u2"]

    first = retrieve_ctl.retrieve.call_args_list[0].kwargs
    second = retrieve_ctl.retrieve.call_args_list[1].kwargs
    assert first["query"] == "what task should I do next?"
    assert first["strategy"] == "contextual"
    assert second["query"] == "what task should I do next?"
    assert second["strategy"] == "auto"


def test_retrieve_split_without_embeddings_has_no_lexical_fallback_diversity() -> None:
    adapter = _make_adapter()

    similarity = _pipeline(adapter)._candidate_similarity(  # noqa: SLF001
        {"text": "todo buy milk", "score": 0.8},
        {"text": "task buy milk", "score": 0.7},
    )

    assert similarity == 0.0
