from __future__ import annotations

from typing import Mapping

import pytest

from openminion.modules.context.knowledge import (
    CAPABILITY_EXPLAIN,
    CAPABILITY_NEIGHBORHOOD,
    CAPABILITY_PATH,
    CAPABILITY_QUERY,
    CAPABILITY_REFRESH,
    GraphExplainRequest,
    GraphExplainResult,
    GraphNeighborhoodRequest,
    GraphPathRequest,
    GraphPathResult,
    GraphQueryRequest,
    GraphQueryResult,
    GraphRefreshRequest,
    GraphRefreshResult,
    KnowledgeGraphService,
    KnowledgeGraphCapabilities,
    KnowledgeGraphHealth,
    KnowledgeGraphProviderConfig,
    KnowledgeGraphRegistry,
    LAYER_THIRD_BRAIN,
    build_knowledge_graph_service,
)
from openminion.modules.context.knowledge.constants import (
    EVENT_REFRESH_COMPLETED,
    EVENT_REFRESH_FAILED,
    EVENT_REFRESH_STARTED,
)
from openminion.modules.context.knowledge.errors import (
    UnsupportedCapabilityError,
)

from tests.context.knowledge.conformance import assert_provider_conforms


class _ServiceSource:
    contract_version = "v1"

    def __init__(
        self,
        *,
        name: str,
        layer: str,
        capabilities: tuple[str, ...] = (CAPABILITY_QUERY,),
    ) -> None:
        self._name = name
        self._layer = layer
        self._capabilities = capabilities

    @property
    def name(self) -> str:
        return self._name

    @property
    def layer(self) -> str:
        return self._layer

    @property
    def tags(self) -> tuple[str, ...]:
        return ()

    @property
    def capabilities(self) -> KnowledgeGraphCapabilities:
        return KnowledgeGraphCapabilities(advertised=self._capabilities)

    def health(self) -> KnowledgeGraphHealth:
        return KnowledgeGraphHealth(provider=self.name, layer=self.layer, ok=True)

    def query(self, request: GraphQueryRequest) -> GraphQueryResult:
        return GraphQueryResult(
            provider=self.name,
            layer=self.layer,
            diagnostics={"query": request.query},
        )

    def neighborhood(self, request: GraphNeighborhoodRequest) -> GraphQueryResult:
        return GraphQueryResult(provider=self.name, layer=self.layer)

    def path(self, request: GraphPathRequest) -> GraphPathResult:
        return GraphPathResult(provider=self.name, layer=self.layer)

    def explain(self, request: GraphExplainRequest) -> GraphExplainResult:
        return GraphExplainResult(
            provider=self.name,
            layer=self.layer,
            target_id=request.target_id,
        )

    def refresh(self, request: GraphRefreshRequest) -> GraphRefreshResult:
        return GraphRefreshResult(provider=self.name, layer=self.layer, ok=True)


def _factory(*, config: KnowledgeGraphProviderConfig, layer: str) -> _ServiceSource:
    capabilities = tuple(config.options.get("capabilities", (CAPABILITY_QUERY,)))
    return _ServiceSource(name=config.name, layer=layer, capabilities=capabilities)


def test_build_service_instantiates_active_third_brain_provider():
    registry = KnowledgeGraphRegistry()
    registry.register("graphify", _factory)

    service = build_knowledge_graph_service(
        {
            "provider": {
                "active": ["repo_graph"],
                "providers": {"repo_graph": {"provider": "graphify"}},
            }
        },
        registry=registry,
    )

    assert [
        source.name for source in service.list_sources(layer=LAYER_THIRD_BRAIN)
    ] == ["repo_graph"]
    result = service.query(GraphQueryRequest(query="runtime"))[0]
    assert result.provider == "repo_graph"
    assert result.diagnostics["query"] == "runtime"


def test_service_exposes_provider_neutral_operations():
    registry = KnowledgeGraphRegistry()
    registry.register("fake", _factory)
    all_capabilities = (
        CAPABILITY_QUERY,
        CAPABILITY_NEIGHBORHOOD,
        CAPABILITY_PATH,
        CAPABILITY_EXPLAIN,
        CAPABILITY_REFRESH,
    )
    service = build_knowledge_graph_service(
        {
            "provider": {
                "active": ["repo_graph"],
                "providers": {
                    "repo_graph": {
                        "provider": "fake",
                        "options": {"capabilities": all_capabilities},
                    }
                },
            }
        },
        registry=registry,
    )
    source = service.get_source("repo_graph")

    assert_provider_conforms(source)
    assert service.health(layer=LAYER_THIRD_BRAIN)[0].provider == "repo_graph"
    assert (
        service.neighborhood(
            GraphNeighborhoodRequest(entity_id="node:runtime"),
            provider_names=("repo_graph",),
        )[0].provider
        == "repo_graph"
    )
    assert (
        service.path(
            GraphPathRequest(
                source_entity_id="node:runtime",
                target_entity_id="node:memory",
            ),
            provider_names=("repo_graph",),
        )[0].provider
        == "repo_graph"
    )
    assert (
        service.explain(
            GraphExplainRequest(target_id="node:runtime"),
            provider_names=("repo_graph",),
        )[0].target_id
        == "node:runtime"
    )
    assert (
        service.refresh(
            GraphRefreshRequest(),
            provider_names=("repo_graph",),
        )[0].ok
        is True
    )


def test_service_rejects_unsupported_operation_with_typed_error():
    registry = KnowledgeGraphRegistry()
    registry.register("fake", _factory)
    service = build_knowledge_graph_service(
        {
            "provider": {
                "active": ["repo_graph"],
                "providers": {"repo_graph": {"provider": "fake"}},
            }
        },
        registry=registry,
    )

    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        service.path(
            GraphPathRequest(
                source_entity_id="node:runtime",
                target_entity_id="node:memory",
            )
        )

    assert exc_info.value.code == "UNSUPPORTED_CAPABILITY"
    assert exc_info.value.details == {
        "provider": "repo_graph",
        "capability": CAPABILITY_PATH,
    }


def test_service_emits_refresh_telemetry_for_success_and_failure():
    events: list[tuple[str, Mapping[str, str]]] = []

    def _emit(event_type: str, payload: Mapping[str, str]) -> None:
        events.append((event_type, payload))

    service = KnowledgeGraphService(
        sources={
            "repo_graph": _ServiceSource(
                name="repo_graph",
                layer=LAYER_THIRD_BRAIN,
                capabilities=(CAPABILITY_REFRESH,),
            ),
            "stale_graph": _ServiceSource(
                name="stale_graph",
                layer=LAYER_THIRD_BRAIN,
            ),
        },
        emit_event=_emit,
    )

    assert service.refresh(GraphRefreshRequest(), provider_names=("repo_graph",))[0].ok
    with pytest.raises(UnsupportedCapabilityError):
        service.refresh(GraphRefreshRequest(), provider_names=("stale_graph",))

    assert [event_type for event_type, _payload in events] == [
        EVENT_REFRESH_STARTED,
        EVENT_REFRESH_COMPLETED,
        EVENT_REFRESH_STARTED,
        EVENT_REFRESH_FAILED,
    ]
    assert events[1][1] == {
        "provider": "repo_graph",
        "layer": LAYER_THIRD_BRAIN,
        "ok": "true",
    }
    assert events[3][1]["provider"] == "stale_graph"
    assert events[3][1]["error_type"] == "UnsupportedCapabilityError"


def test_service_coenables_graphify_shaped_and_imported_fake_provider():
    registry = KnowledgeGraphRegistry()
    registry.register("graphify", _factory)
    registry.register("imported_fake", _factory)

    service = build_knowledge_graph_service(
        {
            "provider": {
                "active": ["repo_graph", "imported_docs"],
                "providers": {
                    "repo_graph": {"provider": "graphify"},
                    "imported_docs": {"provider": "imported_fake"},
                },
            }
        },
        registry=registry,
    )

    assert [source.name for source in service.list_sources()] == [
        "imported_docs",
        "repo_graph",
    ]
    assert [
        result.provider
        for result in service.query(GraphQueryRequest(query="runtime graph"))
    ] == ["imported_docs", "repo_graph"]
