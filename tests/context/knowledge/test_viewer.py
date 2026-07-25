from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
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
    OpenMinionMemoryGraphFakosProvider,
    inspect_graph_viewer_status,
    launch_graph_viewer,
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
    module.__version__ = "test"
    monkeypatch.setitem(sys.modules, "graphfakos", module)
    return module


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
    assert result.diagnostics == {
        "node_count": 2,
        "edge_count": 1,
        "screen": "explore",
    }


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
    )
    payload = report.to_dict()

    assert payload["ok"] is True
    assert payload["graphfakos"] == {"installed": True, "version": "test"}
    assert payload["second_brain"]["visual_ready"] is True
    assert payload["third_brain"][0]["visual_ready"] is True
    assert payload["third_brain"][0]["tags"] == ["code_graph"]
    assert "openminion graph view --brain second" in payload["next_commands"]
    assert (
        "openminion graph view --brain third --provider repo_graph"
        in payload["next_commands"]
    )


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


def test_missing_graphfakos_reports_viewer_extra(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_openminion_graph_view_parser_registration() -> None:
    from openminion.cli.parser.base import build_parser

    args = build_parser().parse_args(
        ["graph", "view", "--brain", "second", "--dry-run", "--json"]
    )

    assert args.graph_command == "view"
    assert args.brain == "second"
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
