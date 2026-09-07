from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.services.agent.memory.gateway_adapter import MemoryServiceGatewayAdapter


def _make_adapter(*, retrieve_ctl: object | None = None) -> MemoryServiceGatewayAdapter:
    store = InMemoryMemoryStore()
    service = MemoryService(store=store)
    return MemoryServiceGatewayAdapter(
        service,
        agent_id="rmq-a-agent",
        retrieve_ctl=retrieve_ctl,
    )


def _pipeline(adapter: MemoryServiceGatewayAdapter):
    return adapter._pipeline  # noqa: SLF001


def test_build_retrieval_filters_shape() -> None:
    adapter = _make_adapter()
    no_project = _pipeline(adapter)._build_retrieval_filters(  # noqa: SLF001
        session_id="sess-a",
        agent_id="agent-a",
        project_id=None,
        source_types=["mem", "episode"],
        time_window_hours=168,
    )
    with_project = _pipeline(adapter)._build_retrieval_filters(  # noqa: SLF001
        session_id="sess-a",
        agent_id="agent-a",
        project_id="project-a",
        source_types=["doc", "skill"],
        time_window_hours=None,
    )

    assert no_project.scope_keys == ["session:sess-a", "agent:agent-a"]
    assert with_project.scope_keys == [
        "session:sess-a",
        "agent:agent-a",
        "project:project-a",
    ]
    assert no_project.types == ["mem", "episode"]
    assert with_project.time_window_hours is None


def test_retrieve_split_propagates_unexpected_failures() -> None:
    import pytest

    retrieve_ctl = Mock(name="retrieve_ctl")
    retrieve_ctl.retrieve.side_effect = [
        RuntimeError("conv failed"),
        [{"text": "knowledge", "meta": {"unit_id": "u-k"}}],
    ]
    adapter = _make_adapter(retrieve_ctl=retrieve_ctl)
    with pytest.raises(RuntimeError, match="conv failed"):
        _pipeline(adapter)._retrieve_split(  # noqa: SLF001
            retrieve_ctl,
            query="query",
            session_id="sess-a",
            agent_id="agent-a",
            project_id=None,
            k_conversational=3,
            k_knowledge=3,
        )


def test_pipeline_order_split_then_selection() -> None:
    retrieve_ctl = Mock(name="retrieve_ctl")
    retrieve_ctl.retrieve.return_value = [{"text": "x", "meta": {"unit_id": "u1"}}]
    adapter = _make_adapter(retrieve_ctl=retrieve_ctl)
    adapter._config = SimpleNamespace(  # noqa: SLF001
        defaults=SimpleNamespace(
            k_conversational=1,
            k_knowledge=1,
            decay_halflife_days=30,
            recency_weight=0.3,
        )
    )
    _pipeline(adapter)._config = adapter._config  # noqa: SLF001

    order: list[str] = []
    orig_split = _pipeline(adapter)._retrieve_split  # noqa: SLF001
    orig_select = _pipeline(adapter)._select_retrieve_hits  # noqa: SLF001

    def _split(*args, **kwargs):  # type: ignore[no-untyped-def]
        order.append("split")
        return orig_split(*args, **kwargs)

    def _select(*args, **kwargs):  # type: ignore[no-untyped-def]
        order.append("select")
        return orig_select(*args, **kwargs)

    _pipeline(adapter)._retrieve_split = _split  # type: ignore[method-assign] # noqa: SLF001
    _pipeline(adapter)._select_retrieve_hits = _select  # type: ignore[method-assign] # noqa: SLF001

    adapter.build_retrieval_context_with_metadata(
        session_id="sess-a", user_message="hello"
    )
    assert order[:2] == ["split", "select"]
