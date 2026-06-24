from __future__ import annotations

import logging
from typing import Any

from openminion.base.types import Message
from openminion.modules.context.knowledge import (
    GraphContextItem,
    GraphOmittedItem,
    GraphPathEvidence,
    GraphQueryRequest,
    GraphQueryResult,
    GraphSourceRef,
    LAYER_THIRD_BRAIN,
    TAG_CODE_GRAPH,
    TAG_DOCUMENT_GRAPH,
)
from openminion.modules.context.knowledge.constants import (
    EVENT_QUERY_COMPLETED,
    EVENT_QUERY_DEGRADED,
    EVENT_QUERY_FAILED,
    EVENT_QUERY_STARTED,
    EVENT_SOURCE_RESOLVED,
)
from openminion.modules.context.knowledge.errors import (
    UnsupportedCapabilityError,
)
from openminion.services.constants import (
    MEMORY_CAPSULE_STRATEGY_DYNAMIC_TURN,
    MEMORY_CAPSULE_STRATEGY_OFF,
)
from openminion.services.gateway.context import build_turn_context


class _SilentMemory:
    def __init__(self) -> None:
        self.capsule_calls = 0
        self.retrieval_calls = 0

    def build_context_with_metadata(
        self,
        *,
        session_id: str,
        user_message: str,
    ) -> tuple[str, dict[str, str]]:
        del session_id, user_message
        self.capsule_calls += 1
        return "", {}

    def build_retrieval_context_with_metadata(
        self,
        *,
        session_id: str,
        user_message: str,
    ) -> tuple[str, dict[str, str]]:
        del session_id, user_message
        self.retrieval_calls += 1
        return "", {}


class _FakeKnowledgeGraphs:
    def __init__(self, results: tuple[GraphQueryResult, ...]) -> None:
        self.results = results
        self.requests: list[GraphQueryRequest] = []

    def list_sources(self, *, layer: str | None = None) -> tuple[object, ...]:
        if layer == LAYER_THIRD_BRAIN:
            return (object(),)
        return ()

    def query(
        self,
        request: GraphQueryRequest,
        *,
        layer: str | None = None,
    ) -> tuple[GraphQueryResult, ...]:
        assert layer == LAYER_THIRD_BRAIN
        self.requests.append(request)
        return self.results


class _SourceRef:
    def __init__(self, name: str) -> None:
        self.name = name


class _PartiallyFailingKnowledgeGraphs:
    def __init__(self, result: GraphQueryResult) -> None:
        self.result = result
        self.provider_calls: list[str] = []

    def list_sources(self, *, layer: str | None = None) -> tuple[_SourceRef, ...]:
        if layer == LAYER_THIRD_BRAIN:
            return (_SourceRef("repo_graph"), _SourceRef("stale_graph"))
        return ()

    def query(
        self,
        request: GraphQueryRequest,
        *,
        provider_names: tuple[str, ...] | None = None,
        layer: str | None = None,
    ) -> tuple[GraphQueryResult, ...]:
        del request
        assert layer == LAYER_THIRD_BRAIN
        provider = provider_names[0] if provider_names else ""
        self.provider_calls.append(provider)
        if provider == "stale_graph":
            raise UnsupportedCapabilityError("stale graph does not support query")
        return (self.result,)


class _FailingKnowledgeGraphs:
    def list_sources(self, *, layer: str | None = None) -> tuple[object, ...]:
        if layer == LAYER_THIRD_BRAIN:
            return (object(),)
        return ()

    def query(self, request: GraphQueryRequest, *, layer: str | None = None) -> object:
        del request, layer
        raise UnsupportedCapabilityError("query is not supported")


def _emit_capture(events: list[dict[str, Any]]):
    def _emit_memory_event(**kwargs: Any) -> None:
        events.append(dict(kwargs))

    return _emit_memory_event


def _build_context(
    *,
    knowledge_graphs: object | None,
    memory_strategy: str = MEMORY_CAPSULE_STRATEGY_OFF,
    memory_dynamic_retrieval_enabled: bool = False,
    memory: _SilentMemory | None = None,
    events: list[dict[str, Any]] | None = None,
):
    return build_turn_context(
        history=[
            Message(
                channel="console",
                target="local-user",
                body="Where is the runtime graph wiring?",
                metadata={"role": "user"},
            )
        ],
        agent_id="main",
        agent_memory=memory or _SilentMemory(),
        logger=logging.getLogger("tests.context.knowledge.gateway_context"),
        emit_memory_event=_emit_capture(events if events is not None else []),
        session_id="session-1",
        run_id="run-1",
        request_id="request-1",
        channel="console",
        target="local-user",
        user_message="Where is the runtime graph wiring?",
        conversation_id="conversation-1",
        thread_id="thread-1",
        attach_id="attach-1",
        memory_capsule_strategy=memory_strategy,
        memory_capsule_cache={},
        memory_dynamic_retrieval_enabled=memory_dynamic_retrieval_enabled,
        knowledge_graphs=knowledge_graphs,
    )


def test_third_brain_context_appends_static_graph_facts_when_memory_is_off() -> None:
    item = GraphContextItem(
        provider="repo_graph",
        source_graph_id="repo",
        node_or_edge_id="openminion.services.runtime.bootstrap",
        source_ref=GraphSourceRef(
            path="src/openminion/services/runtime/bootstrap.py", line=685
        ),
        snippet="build_gateway_service wires runtime services.",
    )
    result = GraphQueryResult(
        provider="repo_graph",
        layer=LAYER_THIRD_BRAIN,
        tags=(TAG_CODE_GRAPH,),
        items=(item,),
        omitted=(GraphOmittedItem(provider="repo_graph", reason="budget"),),
    )
    service = _FakeKnowledgeGraphs((result,))
    memory = _SilentMemory()
    events: list[dict[str, Any]] = []

    context = _build_context(
        knowledge_graphs=service,
        memory=memory,
        events=events,
    )

    assert memory.capsule_calls == 0
    assert service.requests[0].query == "Where is the runtime graph wiring?"
    assert service.requests[0].include_paths is True
    assert context.history[-1].metadata["graph_scope"] == "provider"
    assert context.history[-1].metadata["context_knowledge_graph"] == "true"
    assert "## Third-brain graph context" in context.history[-1].body
    assert "Provider: repo_graph (code_graph)" in context.history[-1].body
    assert "build_gateway_service wires runtime services." in context.history[-1].body
    assert "bootstrap.py:L685" in context.history[-1].body
    assert [event["event_type"] for event in events] == [
        EVENT_SOURCE_RESOLVED,
        EVENT_QUERY_STARTED,
        EVENT_QUERY_COMPLETED,
    ]
    assert events[-1]["payload"]["knowledge_graph_results"] == "1"
    assert events[-1]["payload"]["knowledge_graph_omitted"] == "1"
    assert events[-1]["payload"]["knowledge_graph_providers"] == "repo_graph"


def test_third_brain_context_composes_after_second_brain_memory_context() -> None:
    item = GraphContextItem(
        provider="docs_graph",
        source_graph_id="docs",
        node_or_edge_id="references/third-brain-knowledge-layer.md",
        source_ref=GraphSourceRef(path="references/third-brain-knowledge-layer.md"),
        snippet="Third brain is static facts, docs, and code graph context.",
    )
    path = GraphPathEvidence(
        provider="docs_graph", nodes=(item,), explanation="doc node"
    )
    result = GraphQueryResult(
        provider="docs_graph",
        layer=LAYER_THIRD_BRAIN,
        tags=(TAG_DOCUMENT_GRAPH,),
        items=(item,),
        paths=(path,),
    )
    memory = _SilentMemory()

    context = _build_context(
        knowledge_graphs=_FakeKnowledgeGraphs((result,)),
        memory_strategy=MEMORY_CAPSULE_STRATEGY_DYNAMIC_TURN,
        memory_dynamic_retrieval_enabled=True,
        memory=memory,
    )

    assert memory.capsule_calls == 1
    assert memory.retrieval_calls == 1
    assert context.history[-1].metadata["graph_scope"] == "provider"
    assert "Provider: docs_graph (document_graph)" in context.history[-1].body
    assert "path references/third-brain-knowledge-layer.md" in context.history[-1].body


def test_third_brain_partial_provider_failure_degrades_with_attribution() -> None:
    item = GraphContextItem(
        provider="repo_graph",
        source_graph_id="repo",
        node_or_edge_id="src/openminion/api/runtime.py",
        source_ref=GraphSourceRef(path="src/openminion/api/runtime.py", line=808),
        snippet="resolve_gateway passes knowledge_graphs into the gateway service.",
    )
    result = GraphQueryResult(
        provider="repo_graph",
        layer=LAYER_THIRD_BRAIN,
        tags=(TAG_CODE_GRAPH,),
        items=(item,),
    )
    service = _PartiallyFailingKnowledgeGraphs(result)
    events: list[dict[str, Any]] = []

    context = _build_context(knowledge_graphs=service, events=events)

    assert service.provider_calls == ["repo_graph", "stale_graph"]
    assert "resolve_gateway passes knowledge_graphs" in context.history[-1].body
    assert [event["event_type"] for event in events] == [
        EVENT_SOURCE_RESOLVED,
        EVENT_QUERY_STARTED,
        EVENT_QUERY_DEGRADED,
        EVENT_QUERY_COMPLETED,
    ]
    assert events[0]["payload"]["knowledge_graph_providers"] == "repo_graph,stale_graph"
    assert events[2]["payload"]["knowledge_graph_degraded"] == "true"
    assert events[2]["payload"]["knowledge_graph_failed_providers"] == "stale_graph"
    assert (
        events[2]["payload"]["knowledge_graph_failed_provider_error_codes"]
        == "stale_graph:UNSUPPORTED_CAPABILITY"
    )


def test_third_brain_failure_emits_typed_event_without_context_injection() -> None:
    events: list[dict[str, Any]] = []

    context = _build_context(
        knowledge_graphs=_FailingKnowledgeGraphs(),
        events=events,
    )

    assert len(context.history) == 1
    assert context.knowledge_graph_context == ""
    assert context.knowledge_graph_meta == {
        "knowledge_graph_context_error_code": "UNSUPPORTED_CAPABILITY",
        "knowledge_graph_context_reason_code": "unsupported_capability",
    }
    assert [event["event_type"] for event in events] == [
        EVENT_SOURCE_RESOLVED,
        EVENT_QUERY_STARTED,
        EVENT_QUERY_FAILED,
    ]
    assert events[-1]["payload"]["error_code"] == "UNSUPPORTED_CAPABILITY"
    assert events[-1]["payload"]["reason_code"] == "unsupported_capability"
