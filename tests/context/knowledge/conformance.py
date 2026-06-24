from __future__ import annotations

from openminion.modules.context.knowledge import (
    CAPABILITY_CITATIONS,
    CAPABILITY_EXPLAIN,
    CAPABILITY_NEIGHBORHOOD,
    CAPABILITY_PATH,
    CAPABILITY_QUERY,
    CAPABILITY_REFRESH,
    GraphExplainRequest,
    GraphNeighborhoodRequest,
    GraphPathRequest,
    GraphQueryRequest,
    GraphRefreshRequest,
    KnowledgeGraphSource,
)
from openminion.modules.context.knowledge.models import GraphQueryResult


def assert_provider_conforms(
    source: KnowledgeGraphSource,
    *,
    query: str = "runtime graph",
    entity_id: str = "node:runtime",
    target_id: str = "node:memory",
) -> None:
    health = source.health()
    assert health.provider == source.name
    assert health.layer == source.layer
    assert isinstance(source.tags, tuple)
    assert source.capabilities.supports(CAPABILITY_QUERY)

    result = source.query(GraphQueryRequest(query=query))
    assert result.provider == source.name
    assert result.layer == source.layer
    if source.capabilities.supports(CAPABILITY_CITATIONS):
        assert result.items or result.paths or result.omitted
        for item in result.items:
            assert item.provider == source.name
            assert item.source_graph_id
            assert item.source_ref.path or item.source_ref.line is not None

    if source.capabilities.supports(CAPABILITY_NEIGHBORHOOD):
        neighborhood = source.neighborhood(
            GraphNeighborhoodRequest(entity_id=entity_id)
        )
        assert neighborhood.provider == source.name
        assert neighborhood.layer == source.layer

    if source.capabilities.supports(CAPABILITY_PATH):
        path = source.path(
            GraphPathRequest(source_entity_id=entity_id, target_entity_id=target_id)
        )
        assert path.provider == source.name
        assert path.layer == source.layer

    if source.capabilities.supports(CAPABILITY_EXPLAIN):
        explain = source.explain(GraphExplainRequest(target_id=entity_id))
        assert explain.provider == source.name
        assert explain.layer == source.layer

    if source.capabilities.supports(CAPABILITY_REFRESH):
        refresh = source.refresh(GraphRefreshRequest())
        assert refresh.provider == source.name
        assert refresh.layer == source.layer


def assert_query_results_are_interchangeable(
    left: GraphQueryResult,
    right: GraphQueryResult,
) -> None:
    assert left.layer == right.layer
    assert left.items
    assert right.items

    left_first = left.items[0]
    right_first = right.items[0]

    assert left_first.node_or_edge_id == right_first.node_or_edge_id
    assert left_first.source_ref.path == right_first.source_ref.path
    assert str(left_first.metadata.get("kind", "")).strip()
    assert str(right_first.metadata.get("kind", "")).strip()
