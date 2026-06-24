from __future__ import annotations

import json

import pytest

from openminion.modules.context.knowledge import (
    CAPABILITY_CITATIONS,
    CAPABILITY_EXPLAIN,
    CAPABILITY_PATH,
    CAPABILITY_PROVENANCE,
    CAPABILITY_QUERY,
    CAPABILITY_REFRESH,
    GraphExplainRequest,
    GraphPathRequest,
    GraphQueryRequest,
    GraphRefreshRequest,
    KnowledgeGraphProviderConfig,
    LAYER_THIRD_BRAIN,
    TAG_CODE_GRAPH,
)
from openminion.modules.context.knowledge.adapters.graphify import (
    GraphifyCommandResult,
    GraphifyKnowledgeGraphSource,
)
from openminion.modules.context.knowledge.errors import (
    UnsupportedCapabilityError,
)
from tests.context.knowledge.conformance import assert_provider_conforms
from tests.context.knowledge.fixtures import (
    TEST_QUERY,
    RUNTIME_NODE_ID,
    write_graphify_payload,
)


def _write_graph(path):
    path.write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "module:brain",
                        "label": "Brain runtime",
                        "snippet": "Brain runtime owns orchestration.",
                        "path": "src/openminion/modules/brain/runtime.py",
                        "line": 12,
                    },
                    {
                        "id": "module:memory",
                        "label": "Memory graph",
                        "snippet": "Memory graph stores durable preferences.",
                        "path": "src/openminion/modules/memory/service.py",
                        "line": 24,
                    },
                ],
                "edges": [
                    {
                        "id": "edge:brain-memory",
                        "source": "module:brain",
                        "target": "module:memory",
                        "kind": "uses",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def _source(tmp_path, *, capabilities=None, command=None):
    graph_path = tmp_path / "graph.json"
    _write_graph(graph_path)
    options = {"graph_path": str(graph_path)}
    if capabilities is not None:
        options["capabilities"] = capabilities
    if command is not None:
        options["command"] = command
    config = KnowledgeGraphProviderConfig(
        name="repo_graph",
        provider="graphify",
        tags=(TAG_CODE_GRAPH,),
        optional_capabilities=(CAPABILITY_PATH, CAPABILITY_EXPLAIN),
        options=options,
    )
    return GraphifyKnowledgeGraphSource(config=config, layer=LAYER_THIRD_BRAIN)


def test_query_returns_cited_graph_items(tmp_path):
    source = _source(tmp_path)

    result = source.query(GraphQueryRequest(query="durable preferences"))

    assert result.provider == "repo_graph"
    assert result.items[0].node_or_edge_id == "module:memory"
    assert result.items[0].source_ref.path.endswith("memory/service.py")
    assert result.items[0].source_ref.line == 24
    assert CAPABILITY_CITATIONS in source.capabilities.advertised
    assert CAPABILITY_PROVENANCE in source.capabilities.advertised


def test_graphify_source_conforms_to_provider_contract(tmp_path):
    source = _source(tmp_path)

    assert_provider_conforms(
        source,
        query="durable preferences",
        entity_id="module:brain",
        target_id="module:memory",
    )


def test_path_returns_edge_evidence(tmp_path):
    source = _source(tmp_path)

    result = source.path(
        GraphPathRequest(
            source_entity_id="module:brain",
            target_entity_id="module:memory",
        )
    )

    assert result.paths
    assert [node.node_or_edge_id for node in result.paths[0].nodes] == [
        "module:brain",
        "module:memory",
    ]
    assert result.paths[0].edges[0]["kind"] == "uses"


def test_explain_returns_node_evidence(tmp_path):
    source = _source(tmp_path)

    result = source.explain(GraphExplainRequest(target_id="module:brain"))

    assert result.target_id == "module:brain"
    assert result.evidence[0].snippet == "Brain runtime owns orchestration."


def test_refresh_uses_injected_command_runner(tmp_path):
    calls = []
    graph_path = tmp_path / "graph.json"
    _write_graph(graph_path)

    def runner(args, timeout):
        calls.append((tuple(args), timeout))
        return GraphifyCommandResult(returncode=0, stdout="{}", stderr="")

    config = KnowledgeGraphProviderConfig(
        name="repo_graph",
        provider="graphify",
        options={
            "graph_path": str(graph_path),
            "command": "graphify",
            "command_args": ["--out", str(graph_path)],
        },
    )
    source = GraphifyKnowledgeGraphSource(config=config, runner=runner)

    result = source.refresh(GraphRefreshRequest())

    assert result.ok is True
    assert calls == [(("graphify", "--out", str(graph_path)), 30.0)]
    assert CAPABILITY_REFRESH in source.capabilities.advertised


def test_unsupported_capability_is_typed_error(tmp_path):
    source = _source(
        tmp_path,
        capabilities=(CAPABILITY_QUERY, CAPABILITY_CITATIONS, CAPABILITY_PROVENANCE),
    )

    with pytest.raises(UnsupportedCapabilityError):
        source.path(
            GraphPathRequest(
                source_entity_id="module:brain",
                target_entity_id="module:memory",
            )
        )


def test_graphify_adapter_reads_pragmagraph_graphify_payload(tmp_path):
    graph_path = tmp_path / "graph.json"
    write_graphify_payload(graph_path)
    config = KnowledgeGraphProviderConfig(
        name="repo_graph",
        provider="graphify",
        tags=(TAG_CODE_GRAPH,),
        optional_capabilities=(CAPABILITY_PATH, CAPABILITY_EXPLAIN),
        options={"graph_path": str(graph_path)},
    )
    source = GraphifyKnowledgeGraphSource(config=config, layer=LAYER_THIRD_BRAIN)

    result = source.query(GraphQueryRequest(query=TEST_QUERY))

    assert result.items
    assert result.items[0].source_ref.path == "src/app.py"
    assert result.items[0].node_or_edge_id == RUNTIME_NODE_ID
    assert result.items[0].metadata["kind"] == "python_class"
