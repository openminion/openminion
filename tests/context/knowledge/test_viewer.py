from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
import threading
import tomllib
from types import ModuleType, SimpleNamespace
from typing import Any, Mapping
import sys

import pytest

from openminion.base.config import OpenMinionConfig
from openminion.cli.config import resolve_cli_roots
from openminion.modules.context.knowledge import (
    GraphViewerUnavailableError,
    LAYER_SECOND_BRAIN,
    LAYER_THIRD_BRAIN,
    UnknownProviderError,
)
from openminion.modules.context.knowledge.viewer import (
    GraphViewerRequest,
    inspect_graph_viewer_status,
    launch_graph_viewer,
)
from openminion.modules.context.knowledge.viewer_memory import (
    OpenMinionMemoryGraphFakosProvider,
)
from openminion.modules.memory.models import MemoryRecord, MemoryRelation
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore


@dataclass(frozen=True)
class _FakeGraphFakosRequest:
    screen: str = "explore"
    query: str = ""
    focus_node_id: str | None = None
    source_node_id: str | None = None
    target_node_id: str | None = None
    max_depth: int = 1
    limit: int = 100
    render_limit: int = 240
    render_engine: str = "svg"
    theme: str = "default"
    layout: str = "force"
    filters: dict[str, str] = field(default_factory=dict)
    evidence_filter: str = ""


@dataclass(frozen=True)
class _FakeNode:
    id: str
    label: str
    kind: str
    summary: str = ""
    tags: tuple[str, ...] = ()
    score: float | None = None
    confidence: float | None = None
    source: str = ""
    timestamps: Mapping[str, str] | None = None
    provenance_ids: tuple[str, ...] = ()
    citation_ids: tuple[str, ...] = ()
    visual: Any | None = None
    provider_payload: Mapping[str, object] | None = None


@dataclass(frozen=True)
class _FakeEdge:
    id: str
    source_id: str
    target_id: str
    kind: str
    label: str = ""
    provider_payload: Mapping[str, object] | None = None


@dataclass(frozen=True)
class _FakeProvenance:
    id: str
    provider_id: str
    source_type: str = ""
    source_label: str = ""
    excerpt: str = ""
    created_at: str = ""
    updated_at: str = ""
    confidence: float | None = None


@dataclass(frozen=True)
class _FakeCitation:
    id: str
    label: str = ""
    uri: str = ""
    path: str = ""
    excerpt: str = ""
    provider_payload: Mapping[str, object] | None = None


@dataclass(frozen=True)
class _FakeVisual:
    color: str = ""
    icon: str = ""
    shape: str = "circle"
    size: int = 1
    group: str = ""
    emphasis: str = ""
    muted: bool = False
    pinned: bool = False
    x: float | None = None
    y: float | None = None


@dataclass(frozen=True)
class _FakeGraph:
    graph_id: str
    label: str
    provider_id: str
    provider_label: str
    graph_role: str
    capabilities: tuple[str, ...]
    nodes: tuple[Any, ...]
    edges: tuple[Any, ...]
    provenance: tuple[Any, ...] = ()
    citations: tuple[Any, ...] = ()
    warnings: tuple[str, ...] = ()
    stats: Mapping[str, object] | None = None
    provider_details: Mapping[str, str] | None = None
    provider_payload: Mapping[str, object] | None = None
    available_facets: Mapping[str, tuple[str, ...]] | None = None


class _FakeEnvelopeProvider:
    provider_id = "provider-envelope"
    provider_label = "Provider Envelope"
    graph_role = "provider_viewer_envelope"

    def __init__(self, envelope_path: str) -> None:
        self.envelope_path = envelope_path

    def load_graph(self, request: _FakeGraphFakosRequest) -> _FakeGraph:
        del request
        return _FakeGraph(
            graph_id="envelope",
            label="Envelope",
            provider_id=self.provider_id,
            provider_label=self.provider_label,
            graph_role=self.graph_role,
            capabilities=("local_preview",),
            nodes=(_FakeNode(id="node:1", label="Node 1", kind="node"),),
            edges=(),
        )


def _install_fake_graphfakos(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = ModuleType("graphfakos")
    module.GraphFakosRequest = _FakeGraphFakosRequest
    module.GraphFakosNode = _FakeNode
    module.GraphFakosEdge = _FakeEdge
    module.GraphFakosProvenance = _FakeProvenance
    module.GraphFakosCitation = _FakeCitation
    module.GraphFakosVisual = _FakeVisual
    module.GraphFakosGraph = _FakeGraph
    module.ProviderEnvelopeGraphProvider = _FakeEnvelopeProvider
    module.render_static_html = lambda provider, request: "<html>GraphFakos</html>"
    module.graph_from_provider_envelope = _graph_from_provider_envelope
    module.workspace_manifest_for_graph = _workspace_manifest_for_graph
    module.__version__ = "test"
    monkeypatch.setitem(sys.modules, "graphfakos", module)
    return module


@dataclass(frozen=True)
class _FakeWorkspaceManifest:
    graph: _FakeGraph
    request: _FakeGraphFakosRequest

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "graphfakos.workspace.v1",
            "graph_id": self.graph.graph_id,
            "provider_id": self.graph.provider_id,
            "viewer_state": {
                "screen": self.request.screen,
                "query": self.request.query,
            },
            "viewer_actions": [
                "search",
                "filter",
                "inspect_node",
                "focus_neighborhood",
                "highlight_path",
                "export_visible_graph",
            ],
            "supported_actions": [],
            "supported_captures": [],
            "default_expansion_requests": [
                {"source_id": self.graph.nodes[0].id, "depth": self.request.max_depth}
            ]
            if self.graph.nodes
            else [],
            "performance_budget": {
                "rendered_node_count": len(self.graph.nodes),
                "rendered_edge_count": len(self.graph.edges),
                "raw_node_count": len(self.graph.nodes),
                "raw_edge_count": len(self.graph.edges),
                "level_of_detail": "visible",
            },
            "provider_status": {
                "provider_id": self.graph.provider_id,
                "provider_label": self.graph.provider_label,
                "graph_role": self.graph.graph_role,
                "capabilities": list(self.graph.capabilities),
            },
            "empty_state": dict(
                (self.graph.provider_payload or {}).get("empty_state", {})
            ),
            "desktop_backend_path": f"/{self.request.screen}",
            "provider_payload": {
                "provider_label": self.graph.provider_label,
                "graph_role": self.graph.graph_role,
            },
        }


def _workspace_manifest_for_graph(
    graph: _FakeGraph,
    request: _FakeGraphFakosRequest,
) -> _FakeWorkspaceManifest:
    return _FakeWorkspaceManifest(graph=graph, request=request)


def _graph_from_provider_envelope(
    envelope: Mapping[str, object],
    *,
    source_path: str,
) -> _FakeGraph:
    del source_path
    nodes = tuple(
        _FakeNode(
            id=str(item.get("id") or ""),
            label=str(item.get("label") or item.get("id") or ""),
            kind=str(item.get("kind") or "node"),
        )
        for item in envelope.get("nodes", ())
        if isinstance(item, Mapping)
    )
    return _FakeGraph(
        graph_id=str(envelope.get("snapshot_id") or "envelope"),
        label="PragmaGraph Envelope",
        provider_id="pragmagraph",
        provider_label="PragmaGraph",
        graph_role="provider_viewer_envelope",
        capabilities=("local_preview",),
        nodes=nodes,
        edges=(),
    )


def _roots(tmp_path):
    return resolve_cli_roots(home_root=tmp_path, data_root=tmp_path / ".openminion")


def _package_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _seed_permutation_memory_graph(db_path: Path) -> None:
    store = SQLiteMemoryStore(db_path)
    records = (
        MemoryRecord(
            id="memory:launch",
            scope="agent:alpha",
            type="decision",
            key="launch",
            title="Launch Viewer",
            content="Inspect memory graph launch readiness.",
            source="validated",
            confidence=0.9,
            tags=("reviewed",),
            created_at="2026-07-21T00:00:00+00:00",
            updated_at="2026-07-21T00:02:00+00:00",
        ),
        MemoryRecord(
            id="memory:viewer-fact",
            scope="agent:alpha",
            type="fact",
            key="viewer-fact",
            title="Viewer Fact",
            content="GraphFakos can inspect the OpenMinion memory graph.",
            source="validated",
            confidence=0.7,
            tags=("reviewed",),
            created_at="2026-07-21T00:00:00+00:00",
            updated_at="2026-07-21T00:04:00+00:00",
        ),
        MemoryRecord(
            id="memory:archived",
            scope="agent:beta",
            type="decision",
            key="archived",
            title="Archived Decision",
            content="Older decision from another agent.",
            source="imported",
            confidence=0.95,
            tags=("draft",),
            created_at="2026-07-21T00:00:00+00:00",
            updated_at="2026-07-21T00:01:00+00:00",
        ),
        MemoryRecord(
            id="memory:workflow",
            scope="session:alpha",
            type="procedure",
            key="workflow",
            title="Operator Workflow",
            content="Run status before visual graph workflow inspection.",
            source="validated",
            confidence=0.85,
            tags=("workflow",),
            created_at="2026-07-21T00:00:00+00:00",
            updated_at="2026-07-21T00:03:00+00:00",
        ),
        MemoryRecord(
            id="memory:late-match",
            scope="agent:alpha",
            type="decision",
            key="late-match",
            title="Late Match",
            content="This matching record protects post-filter limiting.",
            source="validated",
            confidence=0.93,
            tags=("late",),
            created_at="2026-07-21T00:00:00+00:00",
            updated_at="2026-07-21T00:05:00+00:00",
        ),
    )
    for record in records:
        store.put(record)
    for relation in (
        MemoryRelation(
            relation_id="relation:launch-viewer",
            source_record_id="memory:launch",
            target_record_id="memory:viewer-fact",
            relation_type="supports",
            created_at="2026-07-21T00:01:00+00:00",
        ),
        MemoryRelation(
            relation_id="relation:archived-launch",
            source_record_id="memory:archived",
            target_record_id="memory:launch",
            relation_type="corrects",
            created_at="2026-07-21T00:02:00+00:00",
        ),
        MemoryRelation(
            relation_id="relation:launch-workflow",
            source_record_id="memory:launch",
            target_record_id="memory:workflow",
            relation_type="depends_on",
            created_at="2026-07-21T00:03:00+00:00",
        ),
        MemoryRelation(
            relation_id="relation:late-launch",
            source_record_id="memory:late-match",
            target_record_id="memory:launch",
            relation_type="related_to",
            created_at="2026-07-21T00:04:00+00:00",
        ),
    ):
        store.put_relation(relation)


def _example_envelope_path() -> Path:
    return _package_root() / "examples" / "graph-viewer" / "repo-viewer-envelope.json"


def _third_brain_example_config(envelope_path: Path) -> OpenMinionConfig:
    config = OpenMinionConfig()
    config.module_configs["knowledge_graphs"] = {
        "provider": {
            "active": ["repo_graph"],
            "providers": {
                "repo_graph": {
                    "provider": "graphify",
                    "tags": ["code_graph", "document_graph"],
                    "optional_capabilities": ["query", "citations", "provenance"],
                    "options": {"viewer_envelope_path": str(envelope_path)},
                }
            },
        }
    }
    return config


def _third_brain_pragmagraph_config(snapshot_path: Path) -> OpenMinionConfig:
    config = OpenMinionConfig()
    config.module_configs["knowledge_graphs"] = {
        "provider": {
            "active": ["repo_graph"],
            "providers": {
                "repo_graph": {
                    "provider": "pragmagraph",
                    "options": {"snapshot_path": str(snapshot_path)},
                }
            },
        }
    }
    return config


def _remove_pragmagraph_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(sys.modules):
        if name == "pragmagraph" or name.startswith("pragmagraph."):
            monkeypatch.delitem(sys.modules, name, raising=False)


def test_second_brain_dry_run_builds_graph_from_memory_db(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_graphfakos(monkeypatch)
    db_path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(db_path)
    now = "2026-07-21T00:00:00+00:00"
    first = MemoryRecord(
        id="memory:1",
        scope="agent:openminion",
        type="decision",
        key="runtime-db",
        title="Runtime DB",
        content={"summary": "Use OpenMinion memory DB as the second brain."},
        created_at=now,
        updated_at=now,
    )
    second = MemoryRecord(
        id="memory:2",
        scope="agent:openminion",
        type="fact",
        key="viewer",
        title="Viewer Preference",
        content="Open the memory graph visually.",
        created_at=now,
        updated_at=now,
    )
    store.put(first)
    store.put(second)
    store.put_relation(
        MemoryRelation(
            relation_id="relation:1",
            source_record_id=first.id,
            target_record_id=second.id,
            relation_type="supports",
            created_at=now,
        )
    )

    result = launch_graph_viewer(
        config=OpenMinionConfig(),
        roots=_roots(tmp_path),
        request=GraphViewerRequest(
            brain="second",
            dry_run=True,
            memory_db=str(db_path),
        ),
    )

    assert result.layer == LAYER_SECOND_BRAIN
    assert result.provider == "openminion-memory"
    assert result.diagnostics["node_count"] == 2
    assert result.diagnostics["edge_count"] == 1
    assert result.diagnostics["screen"] == "explore"
    assert result.diagnostics["stats"]["records"] == 2
    assert result.diagnostics["stats"]["relations"] == 1
    assert result.diagnostics["capabilities"]
    assert result.diagnostics["provider_details"]["owner"] == "OpenMinion memory"
    assert result.diagnostics["viewer_manifest"]["schema_version"] == (
        "graphfakos.workspace.v1"
    )
    assert result.diagnostics["viewer_manifest"]["provider_id"] == "openminion-memory"
    assert (
        result.diagnostics["viewer_manifest"]["provider_status"]["provider_label"]
        == "OpenMinion Memory"
    )
    assert "inspect_node" in result.diagnostics["viewer_manifest"]["viewer_actions"]


def test_second_brain_current_shortcut_filters_agent_and_session_scopes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_graphfakos(monkeypatch)
    db_path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(db_path)
    now = "2026-07-21T00:00:00+00:00"
    session_record = MemoryRecord(
        id="memory:session",
        scope="session:s1",
        type="fact",
        key="session-memory",
        title="Session Memory",
        content="Only the selected session should appear.",
        created_at=now,
        updated_at=now,
    )
    agent_record = MemoryRecord(
        id="memory:agent",
        scope="agent:a1",
        type="fact",
        key="agent-memory",
        title="Agent Memory",
        content="Only the selected agent should appear.",
        created_at=now,
        updated_at=now,
    )
    other_record = MemoryRecord(
        id="memory:other",
        scope="agent:other",
        type="fact",
        key="other-memory",
        title="Other Memory",
        content="This should be filtered out.",
        created_at=now,
        updated_at=now,
    )
    store.put(session_record)
    store.put(agent_record)
    store.put(other_record)

    result = launch_graph_viewer(
        config=OpenMinionConfig(),
        roots=_roots(tmp_path),
        request=GraphViewerRequest(
            current=True,
            agent_id="a1",
            session_id="s1",
            dry_run=True,
            memory_db=str(db_path),
        ),
    )

    assert result.diagnostics["node_count"] == 2
    assert result.diagnostics["filters"] == {"scope": "session:s1,agent:a1"}
    assert result.diagnostics["stats"]["scope_filter"] == [
        "session:s1",
        "agent:a1",
    ]


def test_current_memory_empty_state_does_not_seed_sample_data(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_graphfakos(monkeypatch)

    result = launch_graph_viewer(
        config=OpenMinionConfig(),
        roots=_roots(tmp_path),
        request=GraphViewerRequest(current=True, dry_run=True),
    )

    assert result.diagnostics["node_count"] == 0
    assert result.diagnostics["empty_state"] == {
        "code": "current_memory_empty",
        "message": (
            "No second-brain memory records matched this view. "
            "No sample data was written."
        ),
        "next_commands": [
            "openminion graph status",
            "openminion graph view --current --dry-run --json",
            'openminion agent --message "remember a useful project fact"',
        ],
        "scope_filter": [],
    }
    assert result.diagnostics["viewer_manifest"]["empty_state"] == {
        "code": "current_memory_empty",
        "message": (
            "No second-brain memory records matched this view. "
            "No sample data was written."
        ),
    }
    assert "did not write sample data" in result.diagnostics["warnings"][0]


def test_viewer_request_exposes_graphfakos_navigation_filters(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_graphfakos(monkeypatch)
    db_path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(db_path)
    now = "2026-07-21T00:00:00+00:00"
    store.put(
        MemoryRecord(
            id="memory:filter",
            scope="agent:openminion",
            type="decision",
            key="filter",
            title="Filterable Memory",
            content="Expose the viewer controls from OpenMinion.",
            source="validated",
            confidence=0.9,
            tags=("reviewed",),
            created_at=now,
            updated_at=now,
        )
    )
    store.put(
        MemoryRecord(
            id="memory:not-decision",
            scope="agent:openminion",
            type="fact",
            key="filtered-type",
            title="Filtered By Type",
            content="This record should not pass the node-kind filter.",
            source="validated",
            confidence=0.95,
            tags=("reviewed",),
            created_at=now,
            updated_at=now,
        )
    )
    store.put(
        MemoryRecord(
            id="memory:low-score",
            scope="agent:openminion",
            type="decision",
            key="filtered-score",
            title="Filtered By Score",
            content="This record should not pass the score filter.",
            source="validated",
            confidence=0.5,
            tags=("reviewed",),
            created_at=now,
            updated_at=now,
        )
    )

    result = launch_graph_viewer(
        config=OpenMinionConfig(),
        roots=_roots(tmp_path),
        request=GraphViewerRequest(
            current=True,
            node_kind="decision",
            edge_kind="supports",
            tag="reviewed",
            source="validated",
            min_score="0.8",
            evidence_filter="with_provenance",
            dry_run=True,
            memory_db=str(db_path),
        ),
    )

    assert result.diagnostics["filters"] == {
        "edge_kind": "supports",
        "min_score": "0.8",
        "node_kind": "decision",
        "source": "validated",
        "tag": "reviewed",
    }
    assert result.diagnostics["evidence_filter"] == "with_provenance"
    assert result.diagnostics["node_count"] == 1
    assert result.diagnostics["stats"]["records"] == 1


@pytest.mark.parametrize(
    ("graph_request", "expected_nodes", "expected_edges"),
    [
        (
            _FakeGraphFakosRequest(filters={"node_kind": "decision"}),
            ("memory:late-match", "memory:launch", "memory:archived"),
            ("relation:late-launch", "relation:archived-launch"),
        ),
        (
            _FakeGraphFakosRequest(filters={"source": "validated", "min_score": "0.8"}),
            ("memory:late-match", "memory:workflow", "memory:launch"),
            ("relation:late-launch", "relation:launch-workflow"),
        ),
        (
            _FakeGraphFakosRequest(
                filters={"tag": "reviewed", "edge_kind": "supports"}
            ),
            ("memory:viewer-fact", "memory:launch"),
            ("relation:launch-viewer",),
        ),
        (
            _FakeGraphFakosRequest(
                filters={"scope": "agent:alpha"},
            ),
            ("memory:late-match", "memory:viewer-fact", "memory:launch"),
            ("relation:launch-viewer", "relation:late-launch"),
        ),
        (
            _FakeGraphFakosRequest(query="workflow"),
            ("memory:workflow",),
            (),
        ),
        (
            _FakeGraphFakosRequest(filters={"tag": "late"}, limit=1),
            ("memory:late-match",),
            (),
        ),
    ],
)
def test_second_brain_provider_applies_filter_permutations(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    graph_request: _FakeGraphFakosRequest,
    expected_nodes: tuple[str, ...],
    expected_edges: tuple[str, ...],
) -> None:
    graphfakos = _install_fake_graphfakos(monkeypatch)
    db_path = tmp_path / "memory.db"
    _seed_permutation_memory_graph(db_path)

    graph = OpenMinionMemoryGraphFakosProvider(
        graphfakos=graphfakos,
        db_path=db_path,
        limit=20,
    ).load_graph(graph_request)

    node_ids = tuple(node.id for node in graph.nodes)
    edge_ids = tuple(edge.id for edge in graph.edges)
    if graph_request.limit == 1:
        assert node_ids == expected_nodes
    else:
        assert set(node_ids) == set(expected_nodes)
    assert set(edge_ids) == set(expected_edges)
    assert graph.stats["records"] == len(expected_nodes)
    assert graph.stats["relations"] == len(expected_edges)


def test_second_brain_provider_adds_openminion_visual_metadata(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graphfakos = _install_fake_graphfakos(monkeypatch)
    db_path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(db_path)
    now = "2026-07-21T00:00:00+00:00"
    store.put(
        MemoryRecord(
            id="memory:decision",
            scope="agent:openminion",
            type="decision",
            key="viewer",
            title="Use visual inspection",
            content={"summary": "OpenMinion users can inspect graph state."},
            confidence=0.9,
            created_at=now,
            updated_at=now,
        )
    )

    graph = OpenMinionMemoryGraphFakosProvider(
        graphfakos=graphfakos,
        db_path=db_path,
        limit=20,
    ).load_graph(_FakeGraphFakosRequest())

    node = graph.nodes[0]
    assert node.visual.icon == "check-circle"
    assert node.visual.group == "decision"
    assert "type:decision" in node.tags
    assert node.provider_payload["memory_type"] == "decision"
    assert isinstance(node.provider_payload["namespace"], dict)
    assert graph.provider_details["refresh_strategy"] == "rerun_viewer_request"
    assert graph.provider_details["mutation_policy"] == "read_only_viewer"
    assert graph.provider_payload["refresh"] == {
        "strategy": "rerun_viewer_request",
        "writes_memory": False,
        "live_patch_stream": False,
    }
    assert (
        graph.provider_payload["mutation_policy"]["durable_memory_writes"]
        == "unsupported_from_viewer"
    )
    assert graph.provider_payload["local_endpoints"]["graph_action"] == "/api/action"
    assert graph.provider_payload["viewer_actions"]
    assert "tag" in graph.available_facets
    assert "type:decision" in graph.available_facets["tag"]
    assert graph.available_facets["node_kind"] == ("decision",)


def test_third_brain_uses_configured_viewer_envelope(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_graphfakos(monkeypatch)
    envelope_path = tmp_path / "viewer.json"
    envelope_path.write_text("{}", encoding="utf-8")
    config = OpenMinionConfig()
    config.module_configs["knowledge_graphs"] = {
        "provider": {
            "active": ["repo_graph"],
            "providers": {
                "repo_graph": {
                    "provider": "graphify",
                    "options": {"viewer_envelope_path": str(envelope_path)},
                }
            },
        }
    }

    result = launch_graph_viewer(
        config=config,
        roots=_roots(tmp_path),
        request=GraphViewerRequest(brain="third", dry_run=True),
    )

    assert result.layer == LAYER_THIRD_BRAIN
    assert result.provider == "provider-envelope"
    assert result.graph_role == "provider_viewer_envelope"
    assert result.diagnostics["node_count"] == 1


def test_viewer_status_reports_readiness_and_next_commands(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_graphfakos(monkeypatch)
    db_path = tmp_path / "memory.db"
    now = "2026-07-21T00:00:00+00:00"
    SQLiteMemoryStore(db_path).put(
        MemoryRecord(
            id="memory:status",
            scope="agent:openminion",
            type="fact",
            key="status",
            title="Status Memory",
            content="Graph status should count real memory records.",
            created_at=now,
            updated_at=now,
        )
    )
    envelope_path = tmp_path / "viewer.json"
    envelope_path.write_text("{}", encoding="utf-8")
    config = OpenMinionConfig()
    config.module_configs["knowledge_graphs"] = {
        "provider": {
            "active": ["repo_graph"],
            "providers": {
                "repo_graph": {
                    "provider": "graphify",
                    "tags": ["code_graph"],
                    "optional_capabilities": ["query", "citations"],
                    "options": {"viewer_envelope_path": str(envelope_path)},
                }
            },
        }
    }

    report = inspect_graph_viewer_status(
        config=config,
        roots=_roots(tmp_path),
        memory_db=str(db_path),
    )
    payload = report.to_dict()

    assert payload["ok"] is True
    assert payload["graphfakos"] == {"installed": True, "version": "test"}
    assert payload["second_brain"]["visual_ready"] is True
    assert payload["second_brain"]["details"]["sample_records"] == 1
    assert payload["third_brain"][0]["visual_ready"] is True
    assert payload["third_brain"][0]["tags"] == ["code_graph"]
    assert "openminion graph view --current" in payload["next_commands"]
    assert (
        "openminion graph view --brain third --provider repo_graph"
        in payload["next_commands"]
    )


def test_viewer_status_reports_missing_envelope_as_not_visual_ready(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_graphfakos(monkeypatch)
    missing_path = tmp_path / "missing-viewer.json"
    config = OpenMinionConfig()
    config.module_configs["knowledge_graphs"] = {
        "provider": {
            "active": ["repo_graph"],
            "providers": {
                "repo_graph": {
                    "provider": "graphify",
                    "options": {"viewer_envelope_path": str(missing_path)},
                }
            },
        }
    }

    report = inspect_graph_viewer_status(config=config, roots=_roots(tmp_path))
    third = report.to_dict()["third_brain"][0]

    assert third["visual_ready"] is False
    assert third["reason"] == "Viewer envelope path is configured but not found yet."
    assert third["details"]["diagnostic_code"] == "viewer_envelope_missing"
    assert third["details"]["viewer_envelope_exists"] is False


def test_viewer_status_requires_pragmagraph_for_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_graphfakos(monkeypatch)
    _remove_pragmagraph_modules(monkeypatch)
    original_import_module = importlib.import_module

    def _blocked_import_module(name: str, package: str | None = None) -> object:
        if name == "pragmagraph":
            raise ModuleNotFoundError(name)
        return original_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", _blocked_import_module)
    snapshot_path = tmp_path / "graph.snapshot.json"
    snapshot_path.write_text("{}", encoding="utf-8")

    report = inspect_graph_viewer_status(
        config=_third_brain_pragmagraph_config(snapshot_path),
        roots=_roots(tmp_path),
    )
    payload = report.to_dict()
    third = payload["third_brain"][0]

    assert third["visual_ready"] is False
    assert third["reason"] == (
        "PragmaGraph snapshot viewing requires the pragmagraph package."
    )
    assert third["next_command"] == "python -m pip install 'openminion[viewer]'"
    assert third["details"]["diagnostic_code"] == "pragmagraph_missing"
    assert third["details"]["pragmagraph_required"] is True
    assert third["details"]["pragmagraph_installed"] is False
    assert third["details"]["snapshot_exists"] is True
    assert "python -m pip install 'openminion[viewer]'" in payload["next_commands"]


def test_viewer_extra_installs_pragmagraph_runtime() -> None:
    pyproject = tomllib.loads((_package_root() / "pyproject.toml").read_text())
    viewer_extra = pyproject["project"]["optional-dependencies"]["viewer"]

    assert "graphfakos>=0.0.8" in viewer_extra
    assert "pragmagraph>=0.0.8" in viewer_extra


def test_multiple_active_third_brain_providers_suggest_provider_flags(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_graphfakos(monkeypatch)
    config = OpenMinionConfig()
    config.module_configs["knowledge_graphs"] = {
        "provider": {
            "active": ["repo_graph", "docs_graph"],
            "providers": {
                "repo_graph": {
                    "provider": "graphify",
                    "options": {"viewer_envelope_path": str(tmp_path / "repo.json")},
                },
                "docs_graph": {
                    "provider": "graphify",
                    "options": {"viewer_envelope_path": str(tmp_path / "docs.json")},
                },
            },
        }
    }

    with pytest.raises(UnknownProviderError) as exc_info:
        launch_graph_viewer(
            config=config,
            roots=_roots(tmp_path),
            request=GraphViewerRequest(brain="third", dry_run=True),
        )

    assert exc_info.value.details["suggested_commands"] == [
        "openminion graph view --brain third --provider repo_graph",
        "openminion graph view --brain third --provider docs_graph",
    ]


def test_missing_graphfakos_reports_viewer_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = __import__

    def _blocked_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "graphfakos":
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _blocked_import)

    with pytest.raises(GraphViewerUnavailableError) as exc_info:
        launch_graph_viewer(
            config=OpenMinionConfig(),
            roots=_roots(Path.cwd()),
            request=GraphViewerRequest(brain="second", dry_run=True),
        )

    assert exc_info.value.details == {
        "package": "graphfakos",
        "extra": "viewer",
        "suggested_command": "python -m pip install 'openminion[viewer]'",
    }


def test_memory_viewer_provider_is_not_top_level_public_export() -> None:
    import openminion.modules.context.knowledge as knowledge

    assert "OpenMinionMemoryGraphFakosProvider" not in knowledge.__all__


def test_openminion_graph_view_parser_registration() -> None:
    from openminion.cli.parser.base import build_parser

    args = build_parser().parse_args(
        [
            "graph",
            "view",
            "--current",
            "--agent",
            "a1",
            "--session",
            "s1",
            "--node-kind",
            "fact",
            "--tag",
            "scope:session:s1",
            "--min-score",
            "0.7",
            "--evidence-filter",
            "with_provenance",
            "--dry-run",
            "--json",
        ]
    )

    assert args.graph_command == "view"
    assert args.current is True
    assert args.agent_id == "a1"
    assert args.session_id == "s1"
    assert args.node_kind == "fact"
    assert args.tag == "scope:session:s1"
    assert args.min_score == "0.7"
    assert args.evidence_filter == "with_provenance"
    assert args.dry_run is True


def test_openminion_graph_status_parser_registration() -> None:
    from openminion.cli.parser.base import build_parser

    args = build_parser().parse_args(
        ["graph", "status", "--provider", "repo_graph", "--json"]
    )

    assert args.graph_command == "status"
    assert args.provider == "repo_graph"
    assert args.json is True


def test_second_brain_static_html_uses_real_graphfakos_shell(tmp_path) -> None:
    pytest.importorskip("graphfakos")
    db_path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(db_path)
    now = "2026-07-21T00:00:00+00:00"
    store.put(
        MemoryRecord(
            id="memory:html",
            scope="agent:openminion",
            type="fact",
            key="html",
            title="HTML Viewer",
            content="Generate the visual app shell.",
            created_at=now,
            updated_at=now,
        )
    )
    html_path = tmp_path / "viewer.html"

    result = launch_graph_viewer(
        config=OpenMinionConfig(),
        roots=_roots(tmp_path),
        request=GraphViewerRequest(
            brain="second",
            memory_db=str(db_path),
            html_out=str(html_path),
        ),
    )

    html = html_path.read_text(encoding="utf-8")
    assert result.mode == "static_html"
    assert result.html_path == str(html_path)
    assert "GraphFakos" in html
    assert "HTML Viewer" in html


def test_second_brain_dry_run_exposes_real_graphfakos_manifest(tmp_path) -> None:
    graphfakos = pytest.importorskip("graphfakos")
    if not hasattr(graphfakos, "workspace_manifest_for_graph"):
        pytest.skip("graphfakos workspace manifest helper is unavailable")
    db_path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(db_path)
    now = "2026-07-21T00:00:00+00:00"
    first = MemoryRecord(
        id="memory:real-runtime",
        scope="agent:openminion",
        type="decision",
        key="real-runtime-db",
        title="Real Runtime DB",
        content="Use the OpenMinion SQLite memory DB as the second brain.",
        created_at=now,
        updated_at=now,
    )
    second = MemoryRecord(
        id="memory:real-viewer",
        scope="agent:openminion",
        type="fact",
        key="real-viewer",
        title="Real Viewer",
        content="Render and inspect the second-brain graph through GraphFakos.",
        created_at=now,
        updated_at=now,
    )
    store.put(first)
    store.put(second)
    store.put_relation(
        MemoryRelation(
            relation_id="relation:real-runtime-viewer",
            source_record_id=first.id,
            target_record_id=second.id,
            relation_type="supports",
            created_at=now,
        )
    )

    result = launch_graph_viewer(
        config=OpenMinionConfig(),
        roots=_roots(tmp_path),
        request=GraphViewerRequest(
            brain="second",
            dry_run=True,
            memory_db=str(db_path),
            screen="provider_status",
        ),
    )

    manifest = result.diagnostics["viewer_manifest"]
    provider_status = manifest["provider_status"]
    assert result.provider == "openminion-memory"
    assert result.layer == LAYER_SECOND_BRAIN
    assert result.diagnostics["node_count"] == 2
    assert result.diagnostics["edge_count"] == 1
    assert manifest["schema_version"] == "graphfakos.workspace.v1"
    assert manifest["graph_id"] == f"openminion-memory:{db_path.name}"
    assert manifest["provider_id"] == "openminion-memory"
    assert manifest["desktop_backend_path"] == "/provider_status"
    assert manifest["performance_budget"]["rendered_node_count"] == 2
    assert provider_status["provider_label"] == "OpenMinion Memory"
    assert "durable_memory" in provider_status["capabilities"]
    assert "inspect_node" in manifest["viewer_actions"]
    assert manifest["provider_payload"]["graph_role"] == "second_brain_memory"


def test_second_brain_local_server_routes_workbench_actions_safely(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graphfakos = pytest.importorskip("graphfakos")
    captured: dict[str, object] = {}

    def _serve_local_viewer(**kwargs: object) -> SimpleNamespace:
        captured.update(kwargs)
        render_path = kwargs["render_path"]
        assert callable(render_path)
        html = render_path("/explore", {})
        assert "OpenMinion Second-Brain Memory" in html
        return SimpleNamespace(
            url="http://127.0.0.1:8767/explore",
            opened=False,
        )

    monkeypatch.setattr(graphfakos, "serve_local_viewer", _serve_local_viewer)
    db_path = tmp_path / "memory.db"
    now = "2026-07-21T00:00:00+00:00"
    SQLiteMemoryStore(db_path).put(
        MemoryRecord(
            id="memory:readonly",
            scope="agent:openminion",
            type="fact",
            key="readonly",
            title="Read Only Viewer",
            content="Viewer actions must not silently write durable memory.",
            created_at=now,
            updated_at=now,
        )
    )

    result = launch_graph_viewer(
        config=OpenMinionConfig(),
        roots=_roots(tmp_path),
        request=GraphViewerRequest(
            current=True,
            memory_db=str(db_path),
            open_browser=False,
        ),
    )

    handle_action = captured["handle_action"]
    assert callable(handle_action)
    action_result = handle_action(
        "/api/action",
        {
            "action_id": "draft:memory",
            "action_type": "draft_node",
            "target_id": "memory:readonly",
            "label": "Draft memory edit",
            "body": "This must remain provider-owned.",
        },
    )
    capture_result = handle_action(
        "/api/knowledge",
        {
            "text": "Capture from the visual workbench.",
            "link_node_id": "memory:readonly",
        },
    )

    assert result.mode == "server"
    assert captured["handle_action"] is not None
    assert action_result["ok"] is False
    assert action_result["status"]["status"] == "unsupported"
    assert "does not support graph edit actions" in action_result["status"]["message"]
    assert capture_result["ok"] is False
    assert "does not support workbench knowledge capture" in capture_result["error"]


def test_second_brain_provider_matches_graphfakos_conformance(tmp_path) -> None:
    graphfakos = pytest.importorskip("graphfakos")
    testing = pytest.importorskip("graphfakos.testing")
    if not hasattr(testing, "assert_provider_conformance") or not hasattr(
        testing, "GraphFakosProviderConformanceCase"
    ):
        pytest.skip("graphfakos.testing conformance helpers are unavailable")
    db_path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(db_path)
    now = "2026-07-21T00:00:00+00:00"
    first = MemoryRecord(
        id="memory:runtime",
        scope="agent:openminion",
        type="decision",
        key="runtime-db",
        title="Runtime DB",
        content="Use OpenMinion memory DB as the second brain.",
        created_at=now,
        updated_at=now,
    )
    second = MemoryRecord(
        id="memory:viewer",
        scope="agent:openminion",
        type="fact",
        key="viewer",
        title="Viewer",
        content="Open the memory graph visually.",
        created_at=now,
        updated_at=now,
    )
    store.put(first)
    store.put(second)
    store.put_relation(
        MemoryRelation(
            relation_id="relation:runtime-viewer",
            source_record_id=first.id,
            target_record_id=second.id,
            relation_type="supports",
            created_at=now,
        )
    )
    provider = OpenMinionMemoryGraphFakosProvider(
        graphfakos=graphfakos,
        db_path=db_path,
        limit=20,
    )

    result = testing.assert_provider_conformance(
        testing.GraphFakosProviderConformanceCase(
            provider=provider,
            expected_role="second_brain_memory",
            expected_provider="OpenMinion Memory",
            required_capabilities=("durable_memory", "local_preview"),
            artifact_path=tmp_path / "memory.graphfakos.json",
        )
    )

    assert result.replay_graph is not None
    assert result.report["graph"]["provider_id"] == "openminion-memory"
    assert "Runtime DB" in result.html
    assert "supports" in result.html


def test_third_brain_example_fixture_status_and_html(tmp_path) -> None:
    pytest.importorskip("graphfakos")
    config = _third_brain_example_config(_example_envelope_path())

    status = inspect_graph_viewer_status(config=config, roots=_roots(tmp_path))
    status_payload = status.to_dict()
    third_brain = status_payload["third_brain"][0]
    assert third_brain["provider"] == "repo_graph"
    assert third_brain["visual_ready"] is True
    assert third_brain["details"]["viewer_envelope_exists"] is True

    html_path = tmp_path / "third-brain-viewer.html"
    result = launch_graph_viewer(
        config=config,
        roots=_roots(tmp_path),
        request=GraphViewerRequest(
            brain="third",
            provider="repo_graph",
            html_out=str(html_path),
        ),
    )
    html = html_path.read_text(encoding="utf-8")

    assert result.mode == "static_html"
    assert result.provider == "openminion-example"
    assert "GraphFakos" in html
    assert "Graph Canvas" in html
    assert "Gateway Context" in html
    assert "Graph Viewer Bridge" in html
    assert "docs/runtime-surfaces.md" in html


def test_third_brain_example_fixture_matches_graphfakos_conformance(tmp_path) -> None:
    graphfakos = pytest.importorskip("graphfakos")
    testing = pytest.importorskip("graphfakos.testing")
    if not hasattr(testing, "assert_provider_conformance") or not hasattr(
        testing, "GraphFakosProviderConformanceCase"
    ):
        pytest.skip("graphfakos.testing conformance helpers are unavailable")
    provider = graphfakos.ProviderEnvelopeGraphProvider(str(_example_envelope_path()))

    result = testing.assert_provider_conformance(
        testing.GraphFakosProviderConformanceCase(
            provider=provider,
            expected_role="provider_viewer_envelope",
            expected_provider="openminion-example",
            required_capabilities=("content_preview", "local_preview"),
            artifact_path=tmp_path / "repo.graphfakos.json",
        )
    )

    assert result.replay_graph is not None
    assert result.graph.provider_id == "openminion-example"
    assert "Gateway Context" in result.html
    assert "uses_context_graph" in result.html


def test_static_html_renders_in_playwright_when_chromium_available(tmp_path) -> None:
    playwright_sync = pytest.importorskip("playwright.sync_api")
    db_path = tmp_path / "memory.db"
    store = SQLiteMemoryStore(db_path)
    now = "2026-07-21T00:00:00+00:00"
    store.put(
        MemoryRecord(
            id="memory:browser",
            scope="agent:openminion",
            type="fact",
            key="browser-viewer",
            title="Browser Viewer Smoke",
            content="Render the GraphFakos shell in a real browser when available.",
            created_at=now,
            updated_at=now,
        )
    )
    html_path = tmp_path / "browser-viewer.html"
    launch_graph_viewer(
        config=OpenMinionConfig(),
        roots=_roots(tmp_path),
        request=GraphViewerRequest(
            brain="second",
            memory_db=str(db_path),
            html_out=str(html_path),
        ),
    )

    page_probe = _playwright_page_probe(
        playwright_sync=playwright_sync,
        page_uri=html_path.as_uri(),
    )
    page_text = str(page_probe["text"])
    assert "GraphFakos" in page_text
    assert "Browser Viewer Smoke" in page_text
    assert "Active Query" in page_text
    assert "Tools" in page_text
    assert "Evidence" in page_text
    assert page_probe["canvas_visible"] is True
    assert page_probe["toolbar_present"] is True
    assert page_probe["node_count"] >= 1
    assert page_probe["graph_json_node_count"] == 1
    assert page_probe["inspect_overlay"] is True


def _playwright_page_probe(*, playwright_sync: Any, page_uri: str) -> dict[str, object]:
    box: dict[str, object] = {}

    def _runner() -> None:
        try:
            manager = playwright_sync.sync_playwright().start()
        except Exception as exc:  # noqa: BLE001
            box["skip"] = f"playwright unavailable: {exc}"
            return
        try:
            try:
                browser = manager.chromium.launch(headless=True)
            except Exception as exc:  # noqa: BLE001
                box["skip"] = f"chromium browser binary not available: {exc}"
                return
            try:
                page = browser.new_page()
                page.goto(page_uri)
                viewer = page.locator("graphfakos-viewer")
                box["probe"] = {
                    "text": page.locator("body").inner_text(timeout=10_000),
                    "canvas_visible": page.locator(
                        "[aria-label='GraphFakos graph canvas']"
                    ).is_visible(timeout=10_000),
                    "toolbar_present": page.locator(
                        "[aria-label='Graph filters']"
                    ).count()
                    == 1,
                    "node_count": page.locator("[data-gf-graph-item='node']").count(),
                    "inspect_overlay": page.locator(
                        "[data-gf-inspect-overlay='true']"
                    ).count()
                    == 1,
                    "graph_json_node_count": viewer.evaluate(
                        "(element) => JSON.parse("
                        "element.getAttribute('data-graph-json') || '{}'"
                        ").nodes.length"
                    ),
                }
            except Exception as exc:  # noqa: BLE001
                box["error"] = f"playwright page probe failed: {exc}"
            finally:
                browser.close()
        finally:
            manager.stop()

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    worker.join(timeout=60)
    if worker.is_alive():
        pytest.skip("chromium browser smoke timed out")
    if "skip" in box:
        pytest.skip(str(box["skip"]))
    if "error" in box:
        pytest.fail(str(box["error"]))
    probe = box.get("probe", {})
    return dict(probe) if isinstance(probe, dict) else {}
