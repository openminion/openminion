from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from sophiagraph import (
    GraphBackendQuery,
    KuzuGraphBackendAdapter,
    MemoryNamespace,
    MemoryRecord,
    MemoryRelation,
    Neo4jGraphBackendAdapter,
    build_graph_export_batch,
)


def _ns() -> MemoryNamespace:
    return MemoryNamespace(tenant_id="tenant", agent_id="openminion", graph_id="main")


def _record(record_id: str, title: str) -> MemoryRecord:
    return MemoryRecord(
        id=record_id,
        scope="agent:openminion",
        type="fact",
        key=record_id,
        title=title,
        content={"text": title},
        created_at="2026-06-03T00:00:00+00:00",
        updated_at="2026-06-03T00:00:00+00:00",
        namespace=_ns(),
        meta={"properties": {"kind": "fixture"}},
    )


class _FakeNeo4jRow:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key: str):
        return self._data[key]


class _FakeNeo4jResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = [_FakeNeo4jRow(row) for row in rows]

    def __iter__(self):
        return iter(self._rows)


class _FakeNeo4jSession:
    def __init__(self, state: dict[str, object]) -> None:
        self._state = state

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def run(self, statement: str, params: dict[str, object]):
        tag = statement.splitlines()[0].strip()
        nodes = self._state["nodes"]
        edges = self._state["edges"]
        meta = self._state["meta"]
        if tag.startswith("// sg_op:ensure_"):
            return _FakeNeo4jResult([])
        if tag == "// sg_op:delete_edges":
            for edge_id in params.get("edge_ids", []):
                edges.pop(edge_id, None)
            return _FakeNeo4jResult([])
        if tag == "// sg_op:delete_nodes":
            for node_id in params.get("node_ids", []):
                nodes.pop(node_id, None)
            return _FakeNeo4jResult([])
        if tag == "// sg_op:upsert_node":
            nodes[params["node_id"]] = dict(params)
            return _FakeNeo4jResult([])
        if tag == "// sg_op:upsert_edge":
            edges[params["edge_id"]] = dict(params)
            return _FakeNeo4jResult([])
        if tag == "// sg_op:upsert_meta":
            meta[params["meta_key"]] = params["meta_value"]
            return _FakeNeo4jResult([])
        if tag == "// sg_op:query_neighbors":
            rows = []
            for edge in edges.values():
                if edge["source_node_id"] != params["start_node_id"]:
                    continue
                target = nodes[edge["target_node_id"]]
                rows.append(
                    {
                        "target_node_id": target["node_id"],
                        "primary_label": target["primary_label"],
                        "labels_json": target["labels_json"],
                        "target_properties_json": target["properties_json"],
                        "target_tenant_id": target.get("tenant_id"),
                        "target_org_id": target.get("org_id"),
                        "target_user_id": target.get("user_id"),
                        "target_agent_id": target.get("agent_id"),
                        "target_session_id": target.get("session_id"),
                        "target_conversation_id": target.get("conversation_id"),
                        "target_project_id": target.get("project_id"),
                        "target_graph_id": target.get("graph_id"),
                        "edge_id": edge["edge_id"],
                        "relation_type": edge["relation_type"],
                        "edge_properties_json": edge["properties_json"],
                        "edge_tenant_id": edge.get("tenant_id"),
                        "edge_org_id": edge.get("org_id"),
                        "edge_user_id": edge.get("user_id"),
                        "edge_agent_id": edge.get("agent_id"),
                        "edge_session_id": edge.get("session_id"),
                        "edge_conversation_id": edge.get("conversation_id"),
                        "edge_project_id": edge.get("project_id"),
                        "edge_graph_id": edge.get("graph_id"),
                    }
                )
            return _FakeNeo4jResult(rows)
        raise AssertionError(f"unexpected query tag: {tag}")


class _FakeNeo4jDriver:
    def __init__(self) -> None:
        self._state = {"nodes": {}, "edges": {}, "meta": {}}

    def session(self, database=None):  # noqa: ARG002
        return _FakeNeo4jSession(self._state)

    def close(self) -> None:
        return None


class _FakeNeo4jGraphDatabase:
    def driver(self, uri: str, auth=None):  # noqa: ARG002
        return _FakeNeo4jDriver()


class _FakeNeo4jModule:
    GraphDatabase = _FakeNeo4jGraphDatabase()


def test_openminion_can_query_public_kuzu_backend(tmp_path: Path) -> None:
    pytest.importorskip("kuzu")
    backend = KuzuGraphBackendAdapter(tmp_path / "graph.kuzu")
    batch = build_graph_export_batch(
        batch_id="openminion-direct",
        records=[_record("rec-a", "A"), _record("rec-b", "B")],
        relations=[
            MemoryRelation(
                relation_id="rel-a-b",
                source_record_id="rec-a",
                target_record_id="rec-b",
                relation_type="supports",
                created_at="2026-06-03T00:00:00+00:00",
            )
        ],
    )
    backend.upsert_batch(batch)

    result = backend.query(
        GraphBackendQuery(
            query_id="neighbors-1",
            kind="neighbors",
            start_node_id="rec-a",
            relation_types=["supports"],
            namespace=_ns(),
        )
    )

    assert result.unsupported_reason is None
    assert [row.node_ids for row in result.rows] == [["rec-b"]]
    assert result.rows[0].edge_ids == ["rel-a-b"]


def test_openminion_can_query_public_neo4j_backend(monkeypatch) -> None:
    real_import_module = importlib.import_module

    def _fake_import(name: str):
        if name == "neo4j":
            return _FakeNeo4jModule()
        return real_import_module(name)

    monkeypatch.setattr(importlib, "import_module", _fake_import)
    backend = Neo4jGraphBackendAdapter("neo4j://fixture")
    batch = build_graph_export_batch(
        batch_id="openminion-direct-neo4j",
        records=[_record("rec-a", "A"), _record("rec-b", "B")],
        relations=[
            MemoryRelation(
                relation_id="rel-a-b",
                source_record_id="rec-a",
                target_record_id="rec-b",
                relation_type="supports",
                created_at="2026-06-03T00:00:00+00:00",
            )
        ],
    )
    backend.upsert_batch(batch)

    result = backend.query(
        GraphBackendQuery(
            query_id="neighbors-neo4j",
            kind="neighbors",
            start_node_id="rec-a",
            relation_types=["supports"],
            namespace=_ns(),
        )
    )

    assert result.unsupported_reason is None
    assert [row.node_ids for row in result.rows] == [["rec-b"]]
    assert result.rows[0].edge_ids == ["rel-a-b"]


def test_openminion_graph_backend_fixture_uses_public_sophiagraph_imports_only() -> (
    None
):
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "sophiagraph.graph_backends.base",
        "sophiagraph.graph_backends.fake",
        "sophiagraph.graph_backends.kuzu",
        "sophiagraph.storage.memory",
        "sophiagraph.storage.sqlite",
    }
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
    leaked = imports & forbidden
    assert not leaked, (
        f"fixture reaches into private SophiaGraph paths: {sorted(leaked)}"
    )
