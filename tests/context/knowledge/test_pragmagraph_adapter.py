from __future__ import annotations

import sys
from pathlib import Path

import pytest

from openminion.modules.context.knowledge import (
    CAPABILITY_CITATIONS,
    CAPABILITY_NEIGHBORHOOD,
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
    TAG_DOCUMENT_GRAPH,
)
from openminion.modules.context.knowledge.adapters.pragmagraph import (
    PragmaGraphCommandResult,
    PragmaGraphKnowledgeGraphSource,
)
from openminion.modules.context.knowledge.errors import (
    UnsupportedCapabilityError,
)

from tests.context.knowledge.conformance import assert_provider_conforms
from tests.context.knowledge.fixtures import (
    BOOTSTRAP_NODE_ID,
    RUNTIME_NODE_ID,
    TEST_QUERY,
    ensure_pragmagraph_src_on_path,
    write_pragmagraph_snapshot,
)

ensure_pragmagraph_src_on_path()


def _clear_pragmagraph_modules() -> None:
    for name in tuple(sys.modules):
        if name == "pragmagraph" or name.startswith("pragmagraph."):
            sys.modules.pop(name, None)


def _write_snapshot(path: Path, *, namespace: str = "fixture"):
    _clear_pragmagraph_modules()
    return write_pragmagraph_snapshot(path, namespace=namespace)


def _refresh_root(tmp_path: Path) -> Path:
    root = tmp_path / "fixture-root"
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "app.py").write_text(
        "class RuntimeGraph:\n    pass\n",
        encoding="utf-8",
    )
    return root


def _source(tmp_path: Path, *, capabilities=None, root_path: str = ""):
    snapshot_path = tmp_path / "snapshot.json"
    snapshot = _write_snapshot(snapshot_path)
    options = {"snapshot_path": str(snapshot_path), "namespace": snapshot.namespace}
    if root_path:
        options["root_path"] = root_path
    if capabilities is not None:
        options["capabilities"] = capabilities
    config = KnowledgeGraphProviderConfig(
        name="repo_pragmas",
        provider="pragmagraph",
        tags=(TAG_CODE_GRAPH, TAG_DOCUMENT_GRAPH),
        options=options,
    )
    return PragmaGraphKnowledgeGraphSource(config=config, layer=LAYER_THIRD_BRAIN)


def test_query_returns_cited_pragmagraph_items(tmp_path: Path) -> None:
    source = _source(tmp_path)

    result = source.query(GraphQueryRequest(query=TEST_QUERY, max_results=3))

    assert result.provider == "repo_pragmas"
    assert result.items
    assert result.items[0].provider == "repo_pragmas"
    assert result.items[0].source_graph_id == "fixture"
    assert result.items[0].source_ref.path == "src/app.py"
    assert result.items[0].node_or_edge_id == RUNTIME_NODE_ID
    assert result.items[0].metadata["kind"] == "python_class"
    assert CAPABILITY_QUERY in source.capabilities.advertised
    assert CAPABILITY_NEIGHBORHOOD in source.capabilities.advertised
    assert CAPABILITY_PATH in source.capabilities.advertised
    assert CAPABILITY_CITATIONS in source.capabilities.advertised
    assert CAPABILITY_PROVENANCE in source.capabilities.advertised


def test_pragmagraph_source_conforms_to_provider_contract(tmp_path: Path) -> None:
    source = _source(tmp_path)

    assert_provider_conforms(
        source,
        query=TEST_QUERY,
        entity_id=RUNTIME_NODE_ID,
        target_id=BOOTSTRAP_NODE_ID,
    )


def test_path_maps_package_path_evidence(tmp_path: Path) -> None:
    source = _source(tmp_path)

    result = source.path(
        GraphPathRequest(
            source_entity_id=RUNTIME_NODE_ID,
            target_entity_id=BOOTSTRAP_NODE_ID,
            max_hops=3,
        )
    )

    assert result.provider == "repo_pragmas"
    assert result.paths or result.omitted
    if result.paths:
        assert result.paths[0].nodes
        assert result.paths[0].edges


def test_refresh_api_mode_indexes_and_reloads_snapshot(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    root_path = _refresh_root(tmp_path)
    config = KnowledgeGraphProviderConfig(
        name="repo_pragmas",
        provider="pragmagraph",
        options={
            "snapshot_path": str(snapshot_path),
            "root_path": str(root_path),
            "namespace": "fixture",
        },
    )
    source = PragmaGraphKnowledgeGraphSource(config=config)

    result = source.refresh(GraphRefreshRequest())

    assert result.ok is True
    assert snapshot_path.exists()
    assert result.counts["nodes"] > 0
    assert result.counts["changed_path_count"] > 0
    assert result.counts["added_node_count"] > 0
    assert result.counts["added_edge_count"] > 0
    assert "omitted_reason_counts" in result.diagnostics
    assert CAPABILITY_REFRESH in source.capabilities.advertised
    assert source.health().ok is True


def test_refresh_command_mode_uses_injected_runner(tmp_path: Path) -> None:
    snapshot_path = tmp_path / "snapshot.json"
    root_path = _refresh_root(tmp_path)
    _write_snapshot(snapshot_path)
    calls = []

    def runner(args, timeout):
        calls.append((tuple(args), timeout))
        return PragmaGraphCommandResult(returncode=0, stdout="{}", stderr="")

    config = KnowledgeGraphProviderConfig(
        name="repo_pragmas",
        provider="pragmagraph",
        options={
            "snapshot_path": str(snapshot_path),
            "command": "pragmagraph",
            "command_args": ["index", str(root_path)],
        },
    )
    source = PragmaGraphKnowledgeGraphSource(config=config, runner=runner)

    result = source.refresh(GraphRefreshRequest())

    assert result.ok is True
    assert calls == [(("pragmagraph", "index", str(root_path)), 30.0)]


def test_explain_is_unsupported_until_package_contract_exists(tmp_path: Path) -> None:
    source = _source(tmp_path)

    with pytest.raises(UnsupportedCapabilityError):
        source.explain(GraphExplainRequest(target_id="node:runtime"))
