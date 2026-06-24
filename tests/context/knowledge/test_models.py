from __future__ import annotations

import pytest

from openminion.modules.context.knowledge import (
    CAPABILITY_CITATIONS,
    CAPABILITY_PROMOTES_TO_DURABLE,
    CAPABILITY_QUERY,
    GraphContextItem,
    GraphOmittedItem,
    GraphPathEvidence,
    GraphQueryResult,
    GraphSourceRef,
    KnowledgeGraphCapabilities,
    KnowledgeGraphHealth,
    LAYER_SECOND_BRAIN,
    LAYER_THIRD_BRAIN,
    TAG_CODE_GRAPH,
    TAG_DOCUMENT_GRAPH,
)
from openminion.modules.context.knowledge.errors import (
    InvalidCapabilityError,
    InvalidLayerError,
    InvalidProviderTagError,
)


def test_capabilities_normalizes_to_frozenset():
    caps = KnowledgeGraphCapabilities(
        advertised=[CAPABILITY_QUERY, CAPABILITY_QUERY, CAPABILITY_CITATIONS]
    )
    assert isinstance(caps.advertised, frozenset)
    assert caps.advertised == {CAPABILITY_QUERY, CAPABILITY_CITATIONS}
    assert caps.supports(CAPABILITY_QUERY)
    assert CAPABILITY_QUERY in caps
    assert "explain" not in caps


def test_capabilities_rejects_unknown_capability():
    with pytest.raises(InvalidCapabilityError) as exc_info:
        KnowledgeGraphCapabilities(advertised=["does_not_exist"])
    assert exc_info.value.code == "INVALID_CAPABILITY"


def test_capabilities_rejects_string_argument():
    with pytest.raises(InvalidCapabilityError):
        KnowledgeGraphCapabilities(advertised=CAPABILITY_QUERY)


def test_health_validates_layer():
    health = KnowledgeGraphHealth(
        provider="sophiagraph",
        layer=LAYER_SECOND_BRAIN,
        ok=True,
    )
    assert health.layer == LAYER_SECOND_BRAIN
    assert health.diagnostics == {}


def test_health_rejects_unknown_layer():
    with pytest.raises(InvalidLayerError):
        KnowledgeGraphHealth(provider="x", layer="fourth_brain", ok=True)


def test_graph_context_item_serialization_is_deterministic():
    item = GraphContextItem(
        provider="graphify",
        source_graph_id="repo:main",
        node_or_edge_id="module:openminion.modules.memory",
        source_ref=GraphSourceRef(
            path="openminion/src/openminion/modules/memory/__init__.py", line=1
        ),
        snippet="MemoryService",
        score=0.87,
        metadata={"kind": "module"},
    )
    payload = item.to_dict()
    assert payload["provider"] == "graphify"
    assert payload["source_ref"]["path"].endswith("memory/__init__.py")
    assert payload["score"] == 0.87
    assert payload["metadata"] == {"kind": "module"}


def test_graph_query_result_envelope_aligns_for_both_layers():
    soph = GraphQueryResult(
        provider="sophiagraph",
        layer=LAYER_SECOND_BRAIN,
        items=(
            GraphContextItem(
                provider="sophiagraph",
                source_graph_id="memory",
                node_or_edge_id="mem:1",
                snippet="user prefers concise responses",
            ),
        ),
    )
    graphify = GraphQueryResult(
        provider="graphify",
        layer=LAYER_THIRD_BRAIN,
        tags=(TAG_CODE_GRAPH, TAG_DOCUMENT_GRAPH),
        items=(
            GraphContextItem(
                provider="graphify",
                source_graph_id="repo:main",
                node_or_edge_id="file:README.md",
                source_ref=GraphSourceRef(path="README.md"),
                snippet="OpenMinion",
            ),
        ),
    )

    assert set(soph.to_dict().keys()) == set(graphify.to_dict().keys())
    assert soph.layer == LAYER_SECOND_BRAIN
    assert graphify.layer == LAYER_THIRD_BRAIN
    assert graphify.tags == (TAG_CODE_GRAPH, TAG_DOCUMENT_GRAPH)


def test_graph_query_result_rejects_invalid_tag():
    with pytest.raises(InvalidProviderTagError):
        GraphQueryResult(
            provider="graphify",
            layer=LAYER_THIRD_BRAIN,
            tags=("does_not_exist",),
        )


def test_graph_query_result_rejects_invalid_layer():
    with pytest.raises(InvalidLayerError):
        GraphQueryResult(provider="x", layer="fourth_brain")


def test_graph_query_result_to_dict_includes_paths_and_omitted():
    result = GraphQueryResult(
        provider="graphify",
        layer=LAYER_THIRD_BRAIN,
        items=(
            GraphContextItem(
                provider="graphify",
                source_graph_id="repo:main",
                node_or_edge_id="n1",
            ),
        ),
        paths=(
            GraphPathEvidence(
                provider="graphify",
                explanation="depends_on",
                edges=({"src": "n1", "dst": "n2"},),
            ),
        ),
        omitted=(
            GraphOmittedItem(
                provider="graphify",
                node_or_edge_id="n2",
                reason="over_budget",
            ),
        ),
    )
    payload = result.to_dict()
    assert len(payload["items"]) == 1
    assert len(payload["paths"]) == 1
    assert payload["paths"][0]["explanation"] == "depends_on"
    assert payload["paths"][0]["edges"][0] == {"src": "n1", "dst": "n2"}
    assert len(payload["omitted"]) == 1
    assert payload["omitted"][0]["reason"] == "over_budget"


def test_graph_query_result_frozen_envelope_is_immutable():
    result = GraphQueryResult(
        provider="sophiagraph",
        layer=LAYER_SECOND_BRAIN,
    )
    with pytest.raises(Exception):
        result.provider = "other"  # type: ignore[misc]


def test_promotes_to_durable_capability_is_advertisable():
    caps = KnowledgeGraphCapabilities(
        advertised=[CAPABILITY_PROMOTES_TO_DURABLE, CAPABILITY_QUERY]
    )
    assert caps.supports(CAPABILITY_PROMOTES_TO_DURABLE)
    assert not caps.supports("durable_memory")
