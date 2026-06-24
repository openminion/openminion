from __future__ import annotations

from openminion.modules.context.knowledge import constants as kg_constants


def test_layer_constants_are_in_the_layer_set():
    assert kg_constants.LAYER_SECOND_BRAIN in kg_constants.KNOWLEDGE_GRAPH_LAYERS
    assert kg_constants.LAYER_THIRD_BRAIN in kg_constants.KNOWLEDGE_GRAPH_LAYERS
    assert len(kg_constants.KNOWLEDGE_GRAPH_LAYERS) == 2


def test_provider_tag_constants_are_in_the_tag_set():
    assert kg_constants.TAG_DOCUMENT_GRAPH in kg_constants.KNOWLEDGE_GRAPH_PROVIDER_TAGS
    assert kg_constants.TAG_CODE_GRAPH in kg_constants.KNOWLEDGE_GRAPH_PROVIDER_TAGS
    assert kg_constants.TAG_ARTIFACT_GRAPH in kg_constants.KNOWLEDGE_GRAPH_PROVIDER_TAGS
    assert kg_constants.TAG_HOSTED_GRAPH in kg_constants.KNOWLEDGE_GRAPH_PROVIDER_TAGS
    assert kg_constants.TAG_HYBRID_GRAPH in kg_constants.KNOWLEDGE_GRAPH_PROVIDER_TAGS
    assert len(kg_constants.KNOWLEDGE_GRAPH_PROVIDER_TAGS) == 5


def test_capability_constants_are_in_the_capability_set():
    expected = {
        kg_constants.CAPABILITY_QUERY,
        kg_constants.CAPABILITY_PATH,
        kg_constants.CAPABILITY_NEIGHBORHOOD,
        kg_constants.CAPABILITY_EXPLAIN,
        kg_constants.CAPABILITY_REFRESH,
        kg_constants.CAPABILITY_WATCH,
        kg_constants.CAPABILITY_CITATIONS,
        kg_constants.CAPABILITY_PROVENANCE,
        kg_constants.CAPABILITY_WRITABLE_GRAPH,
        kg_constants.CAPABILITY_DURABLE_MEMORY,
        kg_constants.CAPABILITY_PROMOTE_CANDIDATES,
        kg_constants.CAPABILITY_PROMOTES_TO_DURABLE,
    }
    assert expected == set(kg_constants.KNOWLEDGE_GRAPH_CAPABILITIES)


def test_telemetry_event_names_use_layer_prefix():
    for event in kg_constants.KNOWLEDGE_GRAPH_TELEMETRY_EVENTS:
        assert event.startswith("knowledge_graph.")


def test_layer_set_is_frozen():
    assert isinstance(kg_constants.KNOWLEDGE_GRAPH_LAYERS, frozenset)
    assert isinstance(kg_constants.KNOWLEDGE_GRAPH_PROVIDER_TAGS, frozenset)
    assert isinstance(kg_constants.KNOWLEDGE_GRAPH_CAPABILITIES, frozenset)
    assert isinstance(kg_constants.KNOWLEDGE_GRAPH_TELEMETRY_EVENTS, frozenset)


def test_config_key_is_knowledge_graphs():
    assert kg_constants.KNOWLEDGE_GRAPHS_CONFIG_KEY == "knowledge_graphs"


def test_promote_capabilities_are_distinct_from_durable_memory():
    # The hybrid invariant: promotes_to_durable is a separate capability from
    # durable_memory; hybrids should advertise the former and never the latter.
    assert (
        kg_constants.CAPABILITY_PROMOTES_TO_DURABLE
        != kg_constants.CAPABILITY_DURABLE_MEMORY
    )
    assert (
        kg_constants.CAPABILITY_PROMOTE_CANDIDATES
        != kg_constants.CAPABILITY_DURABLE_MEMORY
    )
