from __future__ import annotations

import pytest

from openminion.modules.context.knowledge import (
    CAPABILITY_CITATIONS,
    CAPABILITY_PROVENANCE,
    CAPABILITY_QUERY,
    DEFAULT_REFRESH_MODE,
    DEFAULT_RETRIEVAL_MAX_CHARS,
    DEFAULT_RETRIEVAL_MAX_RESULTS,
    KnowledgeGraphLayerConfig,
    KnowledgeGraphProviderConfig,
    KnowledgeGraphRefreshConfig,
    KnowledgeGraphRetrievalConfig,
    KnowledgeGraphsConfig,
    LAYER_SECOND_BRAIN,
    LAYER_THIRD_BRAIN,
    TAG_CODE_GRAPH,
    TAG_DOCUMENT_GRAPH,
)
from openminion.modules.context.knowledge.errors import (
    InvalidCapabilityError,
    InvalidLayerError,
    InvalidProviderTagError,
    MultiActiveSecondBrainError,
)


def test_retrieval_config_defaults():
    cfg = KnowledgeGraphRetrievalConfig()
    assert cfg.max_results == DEFAULT_RETRIEVAL_MAX_RESULTS
    assert cfg.max_chars == DEFAULT_RETRIEVAL_MAX_CHARS
    assert cfg.include_paths is True


def test_refresh_config_defaults():
    cfg = KnowledgeGraphRefreshConfig()
    assert cfg.mode == DEFAULT_REFRESH_MODE
    assert cfg.on_start is False
    assert cfg.watch is False


def test_provider_config_validates_tags():
    cfg = KnowledgeGraphProviderConfig(
        name="repo_graph",
        provider="graphify",
        tags=(TAG_CODE_GRAPH, TAG_DOCUMENT_GRAPH),
        required_capabilities=(CAPABILITY_QUERY, CAPABILITY_CITATIONS),
        optional_capabilities=(CAPABILITY_PROVENANCE,),
    )
    assert cfg.tags == (TAG_CODE_GRAPH, TAG_DOCUMENT_GRAPH)
    assert cfg.required_capabilities == (CAPABILITY_QUERY, CAPABILITY_CITATIONS)


def test_provider_config_rejects_invalid_tag():
    with pytest.raises(InvalidProviderTagError):
        KnowledgeGraphProviderConfig(
            name="x",
            provider="graphify",
            tags=("not_a_tag",),
        )


def test_provider_config_rejects_invalid_capability():
    with pytest.raises(InvalidCapabilityError):
        KnowledgeGraphProviderConfig(
            name="x",
            provider="graphify",
            required_capabilities=("not_a_capability",),
        )


def test_layer_config_single_active_second_brain():
    cfg = KnowledgeGraphLayerConfig(
        layer=LAYER_SECOND_BRAIN,
        active=("sophiagraph",),
    )
    assert cfg.active == ("sophiagraph",)


def test_layer_config_multi_active_third_brain():
    cfg = KnowledgeGraphLayerConfig(
        layer=LAYER_THIRD_BRAIN,
        active=("repo_graph", "docs_graph"),
    )
    assert cfg.active == ("repo_graph", "docs_graph")


def test_layer_config_multi_active_second_brain_is_typed_error():
    with pytest.raises(MultiActiveSecondBrainError) as exc_info:
        KnowledgeGraphLayerConfig(
            layer=LAYER_SECOND_BRAIN,
            active=("sophiagraph", "alt_memory"),
        )
    assert exc_info.value.code == "MULTI_ACTIVE_SECOND_BRAIN_REJECTED"
    assert exc_info.value.details["active"] == ["sophiagraph", "alt_memory"]


def test_layer_config_multi_active_second_brain_allowed_with_flag():
    cfg = KnowledgeGraphLayerConfig(
        layer=LAYER_SECOND_BRAIN,
        active=("sophiagraph", "alt_memory"),
        allow_multi_active=True,
    )
    assert cfg.active == ("sophiagraph", "alt_memory")


def test_layer_config_rejects_unknown_layer():
    with pytest.raises(InvalidLayerError):
        KnowledgeGraphLayerConfig(layer="fourth_brain")


def test_layer_config_active_accepts_string_or_list():
    str_form = KnowledgeGraphLayerConfig(
        layer=LAYER_SECOND_BRAIN,
        active="sophiagraph",
    )
    list_form = KnowledgeGraphLayerConfig(
        layer=LAYER_SECOND_BRAIN,
        active=["sophiagraph"],
    )
    assert str_form.active == ("sophiagraph",) == list_form.active


def test_knowledge_graphs_config_defaults():
    cfg = KnowledgeGraphsConfig()
    assert cfg.second_brain.layer == LAYER_SECOND_BRAIN
    assert cfg.second_brain.active == ()
    assert cfg.provider.layer == LAYER_THIRD_BRAIN
    assert cfg.provider.active == ()


def test_knowledge_graphs_config_rejects_layer_mismatch():
    third = KnowledgeGraphLayerConfig(layer=LAYER_THIRD_BRAIN)
    with pytest.raises(InvalidLayerError):
        KnowledgeGraphsConfig(second_brain=third)


def test_knowledge_graphs_config_carries_providers_per_layer():
    soph = KnowledgeGraphProviderConfig(name="sophiagraph", provider="sophiagraph")
    graphify = KnowledgeGraphProviderConfig(
        name="repo_graph",
        provider="graphify",
        tags=(TAG_CODE_GRAPH,),
    )
    cfg = KnowledgeGraphsConfig(
        second_brain=KnowledgeGraphLayerConfig(
            layer=LAYER_SECOND_BRAIN,
            active=("sophiagraph",),
            providers={"sophiagraph": soph},
        ),
        provider=KnowledgeGraphLayerConfig(
            layer=LAYER_THIRD_BRAIN,
            active=("repo_graph",),
            providers={"repo_graph": graphify},
        ),
    )
    assert "sophiagraph" in cfg.second_brain.providers
    assert "repo_graph" in cfg.provider.providers
    assert cfg.provider.providers["repo_graph"].provider == "graphify"
