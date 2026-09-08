"""SophiaGraph workspace adapter contract and live-library tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from openminion.modules.brain.adapters.tool.runtime import ToolAdapter
from openminion.modules.context.knowledge import (
    CAPABILITY_CITATIONS,
    CAPABILITY_NEIGHBORHOOD,
    CAPABILITY_PROVENANCE,
    CAPABILITY_QUERY,
    CAPABILITY_REFRESH,
    GraphNeighborhoodRequest,
    GraphPathRequest,
    GraphQueryRequest,
    GraphRefreshRequest,
    KnowledgeGraphProviderConfig,
    KnowledgeGraphRegistry,
    LAYER_THIRD_BRAIN,
    TAG_DOCUMENT_GRAPH,
    build_knowledge_graph_service,
)
from openminion.modules.context.knowledge.adapters.pragmagraph import (
    PragmaGraphKnowledgeGraphSource,
)
from openminion.modules.context.knowledge.adapters import sophiagraph_workspace
from openminion.modules.context.knowledge.adapters.sophiagraph_workspace import (
    SophiagraphWorkspaceKnowledgeGraphSource,
)
from openminion.modules.context.knowledge.errors import (
    KnowledgeGraphError,
    UnsupportedCapabilityError,
)
from openminion.modules.memory.service import MemoryService
from openminion.modules.memory.storage.memory import InMemoryMemoryStore
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.file import REGISTRAR as FILE_REGISTRAR
from openminion.tools.graph import REGISTRAR as GRAPH_REGISTRAR
from tests.context.knowledge.conformance import assert_provider_conforms
from tests.context.knowledge.fixtures import TEST_QUERY, write_pragmagraph_snapshot

pytestmark = pytest.mark.package_integration


def _initialized_workspace(tmp_path: Path) -> tuple[Path, Path]:
    from sophiagraph import MemoryNamespace, initialize_workspace

    workspace_root = tmp_path / "sophia-workspace"
    source_root = tmp_path / "obsidian-vault"
    source_root.mkdir()
    initialize_workspace(
        workspace_root,
        scope="agent:openminion",
        namespace=MemoryNamespace(agent_id="openminion", graph_id="vault"),
        label="OpenMinion vault",
        vault_id="ovga-vault",
    )
    return workspace_root, source_root


def _source(
    workspace_root: Path,
    source_root: Path,
) -> SophiagraphWorkspaceKnowledgeGraphSource:
    return SophiagraphWorkspaceKnowledgeGraphSource(
        config=KnowledgeGraphProviderConfig(
            name="vault_graph",
            provider="sophiagraph_workspace",
            tags=(TAG_DOCUMENT_GRAPH,),
            required_capabilities=(
                CAPABILITY_QUERY,
                CAPABILITY_NEIGHBORHOOD,
                CAPABILITY_REFRESH,
            ),
            options={
                "workspace_root": str(workspace_root),
                "source_root": str(source_root),
            },
        ),
        layer=LAYER_THIRD_BRAIN,
    )


def _write_linked_notes(source_root: Path, marker: str = "OVGA-MARKER") -> None:
    (source_root / "Hub.md").write_text(
        f"# Hub\n\n{marker}\n\nSee [[Detail]].\n",
        encoding="utf-8",
    )
    (source_root / "Detail.md").write_text(
        f"# Detail\n\n{marker} detail.\n",
        encoding="utf-8",
    )


def test_query_does_not_refresh_and_explicit_refresh_returns_cited_vault_items(
    tmp_path: Path,
) -> None:
    workspace_root, source_root = _initialized_workspace(tmp_path)
    _write_linked_notes(source_root)
    source = _source(workspace_root, source_root)

    assert source.query(GraphQueryRequest(query="OVGA-MARKER")).items == ()

    refreshed = source.refresh(GraphRefreshRequest())
    queried = source.query(GraphQueryRequest(query="OVGA-MARKER", max_results=10))

    assert refreshed.ok is True
    assert refreshed.counts["imported"] == 2
    assert {item.source_ref.path for item in queried.items} == {
        "Detail.md",
        "Hub.md",
    }
    assert {item.source_graph_id for item in queried.items} == {"ovga-vault"}
    assert all(item.provider == "vault_graph" for item in queried.items)
    assert source.capabilities.advertised == frozenset(
        {
            CAPABILITY_QUERY,
            CAPABILITY_NEIGHBORHOOD,
            CAPABILITY_REFRESH,
            CAPABILITY_CITATIONS,
            CAPABILITY_PROVENANCE,
        }
    )


def test_query_filters_records_to_the_workspace_vault_id(tmp_path: Path) -> None:
    from sophiagraph import MemoryRecord, load_workspace_status, open_workspace_store

    workspace_root, source_root = _initialized_workspace(tmp_path)
    _write_linked_notes(source_root)
    source = _source(workspace_root, source_root)
    source.refresh(GraphRefreshRequest())
    store = open_workspace_store(workspace_root)
    namespace = load_workspace_status(workspace_root).metadata.namespace
    store.put_record(
        MemoryRecord(
            id="other-vault-record",
            scope="agent:openminion",
            type="artifact_digest",
            title="Other vault",
            content={"text": "OVGA-MARKER other"},
            created_at="2026-09-06T00:00:00+00:00",
            updated_at="2026-09-06T00:00:00+00:00",
            namespace=namespace,
            meta={"vault": {"vault_id": "other-vault", "path": "Other.md"}},
        )
    )

    result = source.query(GraphQueryRequest(query="OVGA-MARKER", max_results=10))

    assert "other-vault-record" not in {item.node_or_edge_id for item in result.items}


def test_neighborhood_preserves_provider_node_and_edge_identity(tmp_path: Path) -> None:
    workspace_root, source_root = _initialized_workspace(tmp_path)
    _write_linked_notes(source_root)
    source = _source(workspace_root, source_root)
    source.refresh(GraphRefreshRequest())
    query = source.query(GraphQueryRequest(query="Hub", max_results=5))
    hub_id = next(
        item.node_or_edge_id for item in query.items if item.source_ref.path == "Hub.md"
    )

    result = source.neighborhood(
        GraphNeighborhoodRequest(entity_id=hub_id, depth=2, max_results=10)
    )

    assert {item.source_ref.path for item in result.items} == {
        "Detail.md",
        "Hub.md",
    }
    assert result.paths
    assert result.paths[0].edges
    edge = result.paths[0].edges[0]
    assert edge["edge_id"]
    assert edge["source_record_id"] == hub_id
    assert edge["target_record_id"]


def test_neighborhood_filters_nodes_and_edges_to_the_workspace_vault(
    tmp_path: Path,
) -> None:
    from sophiagraph import (
        MemoryRecord,
        StructuralLink,
        load_workspace_status,
        open_workspace_store,
    )

    workspace_root, source_root = _initialized_workspace(tmp_path)
    _write_linked_notes(source_root)
    source = _source(workspace_root, source_root)
    source.refresh(GraphRefreshRequest())
    hub_id = next(
        item.node_or_edge_id
        for item in source.query(GraphQueryRequest(query="Hub")).items
        if item.source_ref.path == "Hub.md"
    )
    store = open_workspace_store(workspace_root)
    namespace = load_workspace_status(workspace_root).metadata.namespace
    store.put_record(
        MemoryRecord(
            id="other-vault-record",
            scope="agent:openminion",
            type="artifact_digest",
            title="Other vault",
            content={"text": "Other vault"},
            created_at="2026-09-06T00:00:00+00:00",
            updated_at="2026-09-06T00:00:00+00:00",
            namespace=namespace,
            meta={
                "document": {"path": "Other.md", "title": "Other vault"},
                "vault": {"vault_id": "other-vault", "path": "Other.md"},
            },
        )
    )
    store.put_record(
        MemoryRecord(
            id="isolated-vault-record",
            scope="agent:openminion",
            type="artifact_digest",
            title="Isolated vault note",
            content={"text": "Isolated vault note"},
            created_at="2026-09-06T00:00:00+00:00",
            updated_at="2026-09-06T00:00:00+00:00",
            namespace=namespace,
            meta={
                "document": {"path": "Isolated.md", "title": "Isolated vault note"},
                "vault": {"vault_id": "ovga-vault", "path": "Isolated.md"},
            },
        )
    )
    store.put_link(
        StructuralLink(
            link_id="cross-vault-link",
            source_record_id=hub_id,
            target_record_id="other-vault-record",
            raw_target="Other",
            link_kind="wikilink",
            resolution_status="resolved",
            namespace=namespace,
        )
    )
    store.put_link(
        StructuralLink(
            link_id="isolated-cross-vault-link",
            source_record_id="isolated-vault-record",
            target_record_id="other-vault-record",
            raw_target="Other",
            link_kind="wikilink",
            resolution_status="resolved",
            namespace=namespace,
        )
    )

    result = source.neighborhood(
        GraphNeighborhoodRequest(entity_id=hub_id, depth=2, max_results=10)
    )
    foreign = source.neighborhood(
        GraphNeighborhoodRequest(
            entity_id="other-vault-record",
            depth=1,
            max_results=10,
        )
    )
    isolated = source.neighborhood(
        GraphNeighborhoodRequest(
            entity_id="isolated-vault-record",
            depth=1,
            max_results=10,
        )
    )

    assert {item.source_ref.path for item in result.items} == {"Hub.md", "Detail.md"}
    assert result.paths
    assert all(
        edge["source_record_id"] != "other-vault-record"
        and edge["target_record_id"] != "other-vault-record"
        for edge in result.paths[0].edges
    )
    hub = next(item for item in result.items if item.node_or_edge_id == hub_id)
    assert hub.metadata["degree_out"] == 1
    assert foreign.items == ()
    assert foreign.paths == ()
    assert len(isolated.items) == 1
    assert isolated.items[0].metadata["degree_in"] == 0
    assert isolated.items[0].metadata["degree_out"] == 0
    assert isolated.items[0].metadata["orphan"] is True
    assert isolated.paths[0].edges == ()


def test_adapter_conforms_and_reopens_same_workspace_without_duplicate_sync(
    tmp_path: Path,
) -> None:
    workspace_root, source_root = _initialized_workspace(tmp_path)
    _write_linked_notes(source_root)
    source = _source(workspace_root, source_root)

    source.refresh(GraphRefreshRequest())
    entity_id = (
        source.query(GraphQueryRequest(query="OVGA-MARKER")).items[0].node_or_edge_id
    )
    assert_provider_conforms(source, query="OVGA-MARKER", entity_id=entity_id)

    restarted = _source(workspace_root, source_root)
    refresh = restarted.refresh(GraphRefreshRequest())
    query = restarted.query(GraphQueryRequest(query="OVGA-MARKER"))

    assert refresh.counts["changed"] == 0
    assert len(query.items) == 2


def test_vault_edit_is_visible_only_after_explicit_refresh(tmp_path: Path) -> None:
    workspace_root, source_root = _initialized_workspace(tmp_path)
    _write_linked_notes(source_root)
    source = _source(workspace_root, source_root)
    source.refresh(GraphRefreshRequest())

    (source_root / "Detail.md").write_text(
        "# Detail\n\nOVGA-UPDATED-MARKER\n",
        encoding="utf-8",
    )

    assert source.query(GraphQueryRequest(query="OVGA-UPDATED-MARKER")).items == ()
    refreshed = source.refresh(GraphRefreshRequest())
    updated = source.query(GraphQueryRequest(query="OVGA-UPDATED-MARKER"))

    assert refreshed.counts["changed"] == 1
    assert [item.source_ref.path for item in updated.items] == ["Detail.md"]
    assert "durable_memory" not in source.capabilities


def test_invalid_workspace_and_missing_package_use_typed_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = _source(tmp_path / "missing-workspace", tmp_path / "missing-vault")

    assert missing.health().ok is False
    with pytest.raises(KnowledgeGraphError) as invalid:
        missing.query(GraphQueryRequest(query="anything"))
    assert invalid.value.details["reason_code"] == "workspace_unavailable"
    assert "workspace_root" not in invalid.value.details

    original_import = sophiagraph_workspace.import_module

    def import_without_sophia(name: str):
        if name == "sophiagraph":
            raise ImportError(name)
        return original_import(name)

    monkeypatch.setattr(sophiagraph_workspace, "import_module", import_without_sophia)
    unavailable = _source(tmp_path / "workspace", tmp_path / "vault")
    with pytest.raises(KnowledgeGraphError) as package:
        unavailable.query(GraphQueryRequest(query="anything"))
    assert package.value.details == {
        "provider": "vault_graph",
        "reason_code": "package_unavailable",
    }


def test_missing_source_root_is_normalized_without_leaking_path(tmp_path: Path) -> None:
    workspace_root, _source_root = _initialized_workspace(tmp_path)
    source = _source(workspace_root, tmp_path / "missing-vault")

    with pytest.raises(KnowledgeGraphError) as failure:
        source.refresh(GraphRefreshRequest())

    assert failure.value.details["reason_code"] == "workspace_unavailable"
    assert "missing-vault" not in str(failure.value.details)


def test_adapter_rejects_relative_workspace_roots() -> None:
    with pytest.raises(KnowledgeGraphError) as failure:
        _source(Path("relative-workspace"), Path("relative-vault"))

    assert failure.value.details == {
        "provider": "vault_graph",
        "reason_code": "configuration_invalid",
        "field": "workspace_root",
    }


def test_path_is_not_advertised_or_locally_reconstructed(tmp_path: Path) -> None:
    workspace_root, source_root = _initialized_workspace(tmp_path)
    source = _source(workspace_root, source_root)

    with pytest.raises(UnsupportedCapabilityError):
        source.path(GraphPathRequest(source_entity_id="a", target_entity_id="b"))


def test_file_write_vault_and_repo_graphs_compose_without_memory_promotion(
    tmp_path: Path,
) -> None:
    workspace_root, source_root = _initialized_workspace(tmp_path)
    snapshot_path = tmp_path / "repo-snapshot.json"
    write_pragmagraph_snapshot(snapshot_path)
    registry = KnowledgeGraphRegistry()
    registry.register("sophiagraph_workspace", SophiagraphWorkspaceKnowledgeGraphSource)
    registry.register("pragmagraph", PragmaGraphKnowledgeGraphSource)
    config = {
        "provider": {
            "active": ["vault_graph", "repo_graph"],
            "providers": {
                "vault_graph": {
                    "provider": "sophiagraph_workspace",
                    "tags": ["document_graph"],
                    "options": {
                        "workspace_root": str(workspace_root),
                        "source_root": str(source_root),
                    },
                },
                "repo_graph": {
                    "provider": "pragmagraph",
                    "tags": ["code_graph"],
                    "options": {"snapshot_path": str(snapshot_path)},
                },
            },
        }
    }
    memory = InMemoryMemoryStore()
    memory_service = MemoryService(store=memory)
    service = build_knowledge_graph_service(config, registry=registry)
    tool_registry = ToolRegistry()
    FILE_REGISTRAR.register(tool_registry)
    GRAPH_REGISTRAR.register(tool_registry)
    tools = ToolAdapter(
        workspace_root=source_root,
        runtime_registry=tool_registry,
        memory_service=memory_service,
        knowledge_graph_service=service,
    )
    for name, link in (("Hub.md", "Detail"), ("Detail.md", "Hub")):
        written = tools.execute(
            command={
                "tool_name": "file.write",
                "args": {
                    "path": name,
                    "content": f"# {name[:-3]}\n\n{TEST_QUERY}\n\n[[{link}]]\n",
                },
                "inputs": {"permission_mode": "bypass"},
            },
            session_id="ovga-composition",
            trace_id=f"write-{name}",
        )
        assert written["status"] == "success"
        assert (source_root / name).is_file(), written

    refreshed = tools.execute(
        command={
            "tool_name": "graph.refresh",
            "args": {"source": "vault_graph"},
            "inputs": {
                "confirmation_grant_id": "ovga-composition-refresh",
                "confirmation_source": "policy_replay",
            },
        },
        session_id="ovga-composition",
        trace_id="refresh-vault",
    )
    queried = tools.execute(
        command={
            "tool_name": "graph.query",
            "args": {"source": "vault_graph", "query": TEST_QUERY},
            "inputs": {"permission_mode": "bypass"},
        },
        session_id="ovga-composition",
        trace_id="query-vault",
    )

    assert refreshed["status"] == "success"
    assert refreshed["outputs"]["results"][0]["counts"]["changed"] == 2
    assert queried["status"] == "success"
    vault = service.query(
        GraphQueryRequest(query=TEST_QUERY), provider_names=("vault_graph",)
    )[0]
    repo = service.query(
        GraphQueryRequest(query=TEST_QUERY), provider_names=("repo_graph",)
    )[0]
    restarted = build_knowledge_graph_service(config, registry=registry)
    restart_refresh = restarted.refresh(
        GraphRefreshRequest(), provider_names=("vault_graph",)
    )[0]

    assert [source.name for source in service.list_sources()] == [
        "vault_graph",
        "repo_graph",
    ]
    assert vault.provider == "vault_graph"
    assert {item.source_ref.path for item in vault.items} == {"Hub.md", "Detail.md"}
    assert repo.provider == "repo_graph"
    assert repo.items[0].source_ref.path == "src/app.py"
    assert restart_refresh.counts["changed"] == 0
    assert restarted.query(
        GraphQueryRequest(query=TEST_QUERY), provider_names=("vault_graph",)
    )[0].items
    assert memory.list_all() == []
