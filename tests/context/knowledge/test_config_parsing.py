from __future__ import annotations

import pytest

from openminion.base.config.parser import openminion_config_from_dict
from openminion.modules.context.knowledge import (
    CAPABILITY_CITATIONS,
    CAPABILITY_EXPLAIN,
    CAPABILITY_PATH,
    CAPABILITY_PROVENANCE,
    CAPABILITY_QUERY,
    KNOWLEDGE_GRAPHS_CONFIG_KEY,
    LAYER_THIRD_BRAIN,
    TAG_CODE_GRAPH,
    TAG_DOCUMENT_GRAPH,
    knowledge_graphs_config_from_mapping,
    resolve_knowledge_graphs_config,
)
from openminion.modules.context.knowledge.errors import (
    MultiActiveSecondBrainError,
)


def test_unset_openminion_config_is_noop():
    base = openminion_config_from_dict({})
    cfg = resolve_knowledge_graphs_config(base)
    assert cfg.second_brain.active == ()
    assert cfg.provider.active == ()


def test_graphify_provider_config_parses_from_openminion_module_configs():
    base = openminion_config_from_dict(
        {
            KNOWLEDGE_GRAPHS_CONFIG_KEY: {
                "provider": {
                    "active": ["repo_graph"],
                    "providers": {
                        "repo_graph": {
                            "provider": "graphify",
                            "enabled": True,
                            "tags": [TAG_CODE_GRAPH, TAG_DOCUMENT_GRAPH],
                            "graph_path": "./graphify-out/graph.json",
                            "required_capabilities": [
                                CAPABILITY_QUERY,
                                CAPABILITY_CITATIONS,
                                CAPABILITY_PROVENANCE,
                            ],
                            "optional_capabilities": [
                                CAPABILITY_PATH,
                                CAPABILITY_EXPLAIN,
                            ],
                            "retrieval": {
                                "max_results": 7,
                                "max_chars": 1200,
                                "include_paths": False,
                            },
                        }
                    },
                }
            }
        }
    )

    cfg = resolve_knowledge_graphs_config(base)
    provider = cfg.provider.providers["repo_graph"]
    assert cfg.provider.layer == LAYER_THIRD_BRAIN
    assert cfg.provider.active == ("repo_graph",)
    assert provider.provider == "graphify"
    assert provider.tags == (TAG_CODE_GRAPH, TAG_DOCUMENT_GRAPH)
    assert provider.options["graph_path"] == "./graphify-out/graph.json"
    assert provider.retrieval.max_results == 7
    assert provider.retrieval.max_chars == 1200
    assert provider.retrieval.include_paths is False


def test_third_brain_multi_active_parses():
    cfg = knowledge_graphs_config_from_mapping(
        {
            "provider": {
                "active": ["repo_graph", "docs_graph"],
                "providers": {
                    "repo_graph": {"provider": "graphify"},
                    "docs_graph": {"provider": "graphify"},
                },
            }
        }
    )
    assert cfg.provider.active == ("repo_graph", "docs_graph")


def test_second_brain_multi_active_raises_typed_error():
    with pytest.raises(MultiActiveSecondBrainError):
        knowledge_graphs_config_from_mapping(
            {
                "second_brain": {
                    "active": ["sophiagraph", "other_memory"],
                    "providers": {
                        "sophiagraph": {"provider": "sophiagraph"},
                        "other_memory": {"provider": "external"},
                    },
                }
            }
        )
