"""GraphFakos-backed viewer helpers for OpenMinion graph state."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
from typing import Any, Mapping

from openminion.base.config import OpenMinionConfig
from openminion.cli.config import CLIRoots
from openminion.modules.context.knowledge.config import (
    KnowledgeGraphLayerConfig,
    KnowledgeGraphProviderConfig,
    resolve_knowledge_graphs_config,
)
from openminion.modules.context.knowledge.constants import (
    LAYER_SECOND_BRAIN,
    LAYER_THIRD_BRAIN,
    PROVIDER_PRAGMAGRAPH,
)
from openminion.modules.context.knowledge.errors import (
    GraphViewerSourceError,
    GraphViewerUnavailableError,
    UnknownProviderError,
)
from openminion.modules.memory.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH

_VIEWER_ENVELOPE_PATH_OPTION = "viewer_envelope_path"
_OPENMINION_MEMORY_PROVIDER_ID = "openminion-memory"

_MEMORY_TYPE_VISUALS = {
    "decision": {"color": "#2563eb", "icon": "check-circle", "shape": "hexagon"},
    "fact": {"color": "#059669", "icon": "file-text", "shape": "circle"},
    "preference": {"color": "#d97706", "icon": "sliders", "shape": "diamond"},
    "procedure": {"color": "#7c3aed", "icon": "list-checks", "shape": "square"},
    "episode": {"color": "#0891b2", "icon": "clock", "shape": "circle"},
}
_DEFAULT_MEMORY_VISUAL = {
    "color": "#475569",
    "icon": "brain",
    "shape": "circle",
}


@dataclass(frozen=True)
class GraphViewerRequest:
    brain: str = "third"
    provider: str = ""
    screen: str = "explore"
    query: str = ""
    focus_node_id: str = ""
    source_node_id: str = ""
    target_node_id: str = ""
    max_depth: int = 1
    limit: int = 100
    render_limit: int = 240
    render_engine: str = "svg"
    theme: str = "default"
    layout: str = "force"
    host: str = "127.0.0.1"
    port: int = 8767
    open_browser: bool = True
    dry_run: bool = False
    html_out: str = ""
    memory_db: str = ""


@dataclass(frozen=True)
class GraphViewerLaunchResult:
    provider: str
    layer: str
    graph_role: str
    mode: str
    url: str = ""
    html_path: str = ""
    opened: bool = False
    diagnostics: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "layer": self.layer,
            "graph_role": self.graph_role,
            "mode": self.mode,
            "url": self.url,
            "html_path": self.html_path,
            "opened": self.opened,
            "diagnostics": dict(self.diagnostics or {}),
        }


@dataclass(frozen=True)
class GraphViewerProviderStatus:
    provider: str
    layer: str
    adapter: str
    active: bool
    enabled: bool
    visual_ready: bool
    reason: str = ""
    next_command: str = ""
    tags: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    details: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "layer": self.layer,
            "adapter": self.adapter,
            "active": self.active,
            "enabled": self.enabled,
            "visual_ready": self.visual_ready,
            "reason": self.reason,
            "next_command": self.next_command,
            "tags": list(self.tags),
            "capabilities": list(self.capabilities),
            "details": dict(self.details or {}),
        }


@dataclass(frozen=True)
class GraphViewerStatusReport:
    graphfakos_installed: bool
    graphfakos_version: str
    second_brain: GraphViewerProviderStatus
    third_brain: tuple[GraphViewerProviderStatus, ...] = ()
    next_commands: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return bool(
            self.graphfakos_installed
            and (
                self.second_brain.visual_ready
                or any(provider.visual_ready for provider in self.third_brain)
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "graphfakos": {
                "installed": self.graphfakos_installed,
                "version": self.graphfakos_version,
            },
            "second_brain": self.second_brain.to_dict(),
            "third_brain": [provider.to_dict() for provider in self.third_brain],
            "next_commands": list(self.next_commands),
        }


def inspect_graph_viewer_status(
    *,
    config: OpenMinionConfig,
    roots: CLIRoots,
    provider: str = "",
    memory_db: str = "",
) -> GraphViewerStatusReport:
    graphfakos_installed, graphfakos_version = _graphfakos_install_status()
    second_brain = _second_brain_status(
        roots=roots,
        memory_db=memory_db,
        graphfakos_installed=graphfakos_installed,
    )
    third_brain = _third_brain_statuses(
        config=config,
        roots=roots,
        selected_provider=provider,
        graphfakos_installed=graphfakos_installed,
    )
    next_commands = _status_next_commands(
        graphfakos_installed=graphfakos_installed,
        second_brain=second_brain,
        third_brain=third_brain,
    )
    return GraphViewerStatusReport(
        graphfakos_installed=graphfakos_installed,
        graphfakos_version=graphfakos_version,
        second_brain=second_brain,
        third_brain=third_brain,
        next_commands=next_commands,
    )


def launch_graph_viewer(
    *,
    config: OpenMinionConfig,
    roots: CLIRoots,
    request: GraphViewerRequest,
) -> GraphViewerLaunchResult:
    graphfakos = _load_graphfakos()
    graph_request = _graphfakos_request(graphfakos, request)
    provider = _viewer_provider(
        graphfakos=graphfakos,
        config=config,
        roots=roots,
        request=request,
    )
    layer = _layer_from_brain(request.brain)
    if request.dry_run:
        graph = provider.load_graph(graph_request)
        return GraphViewerLaunchResult(
            provider=provider.provider_id,
            layer=layer,
            graph_role=provider.graph_role,
            mode="dry_run",
            diagnostics={
                "node_count": len(graph.nodes),
                "edge_count": len(graph.edges),
                "screen": graph_request.screen,
            },
        )
    if request.html_out:
        html_path = _write_static_html(
            graphfakos=graphfakos,
            provider=provider,
            graph_request=graph_request,
            html_out=request.html_out,
            roots=roots,
        )
        return GraphViewerLaunchResult(
            provider=provider.provider_id,
            layer=layer,
            graph_role=provider.graph_role,
            mode="static_html",
            html_path=str(html_path),
        )
    server_result = _serve_viewer(
        graphfakos=graphfakos,
        provider=provider,
        graph_request=graph_request,
        request=request,
    )
    return GraphViewerLaunchResult(
        provider=provider.provider_id,
        layer=layer,
        graph_role=provider.graph_role,
        mode="server",
        url=str(getattr(server_result, "url", "")),
        opened=bool(getattr(server_result, "opened", False)),
        diagnostics=dict(getattr(server_result, "diagnostics", {}) or {}),
    )


def _load_graphfakos() -> Any:
    try:
        import graphfakos
    except ModuleNotFoundError as exc:
        raise GraphViewerUnavailableError(
            "Graph viewer support requires GraphFakos. Install openminion[viewer] "
            "or install graphfakos in this environment.",
            details={
                "package": "graphfakos",
                "extra": "viewer",
                "suggested_command": "python -m pip install 'openminion[viewer]'",
            },
        ) from exc
    return graphfakos


def _graphfakos_install_status() -> tuple[bool, str]:
    try:
        graphfakos = importlib.import_module("graphfakos")
    except ModuleNotFoundError:
        return False, ""
    return True, str(getattr(graphfakos, "__version__", "") or "")


def _graphfakos_request(graphfakos: Any, request: GraphViewerRequest) -> Any:
    return graphfakos.GraphFakosRequest(
        screen=request.screen,
        query=request.query,
        focus_node_id=request.focus_node_id or None,
        source_node_id=request.source_node_id or None,
        target_node_id=request.target_node_id or None,
        max_depth=max(1, int(request.max_depth)),
        limit=max(1, int(request.limit)),
        render_limit=max(1, int(request.render_limit)),
        render_engine=request.render_engine,
        theme=request.theme,
        layout=request.layout,
    )


def _viewer_provider(
    *,
    graphfakos: Any,
    config: OpenMinionConfig,
    roots: CLIRoots,
    request: GraphViewerRequest,
) -> Any:
    if _layer_from_brain(request.brain) == LAYER_SECOND_BRAIN:
        return OpenMinionMemoryGraphFakosProvider(
            graphfakos=graphfakos,
            db_path=_memory_db_path(request, roots=roots),
            limit=max(1, int(request.limit)),
        )
    provider_config = _third_brain_provider_config(config, request)
    return _third_brain_graphfakos_provider(
        graphfakos=graphfakos,
        provider_config=provider_config,
        roots=roots,
        request=request,
    )


def _layer_from_brain(brain: str) -> str:
    value = str(brain or "").strip().lower()
    if value in {"second", "second_brain", "memory"}:
        return LAYER_SECOND_BRAIN
    if value in {"third", "third_brain", "provider"}:
        return LAYER_THIRD_BRAIN
    raise GraphViewerSourceError(
        "brain must be 'second' or 'third'",
        details={"brain": brain},
    )


def _memory_db_path(request: GraphViewerRequest, *, roots: CLIRoots) -> Path:
    if request.memory_db:
        return Path(request.memory_db).expanduser().resolve(strict=False)
    return (roots.data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve(strict=False)


def _second_brain_status(
    *,
    roots: CLIRoots,
    memory_db: str,
    graphfakos_installed: bool,
) -> GraphViewerProviderStatus:
    db_path = _memory_db_path(
        GraphViewerRequest(memory_db=memory_db),
        roots=roots,
    )
    db_exists = db_path.exists()
    sample_records = _memory_db_sample_count(db_path) if db_exists else 0
    if not graphfakos_installed:
        return GraphViewerProviderStatus(
            provider=_OPENMINION_MEMORY_PROVIDER_ID,
            layer=LAYER_SECOND_BRAIN,
            adapter="sophiagraph-sqlite",
            active=True,
            enabled=True,
            visual_ready=False,
            reason="GraphFakos is not installed.",
            next_command="python -m pip install 'openminion[viewer]'",
            capabilities=("durable_memory", "local_preview", "static_export"),
            details={
                "memory_db": str(db_path),
                "memory_db_exists": db_exists,
                "sample_records": sample_records,
            },
        )
    return GraphViewerProviderStatus(
        provider=_OPENMINION_MEMORY_PROVIDER_ID,
        layer=LAYER_SECOND_BRAIN,
        adapter="sophiagraph-sqlite",
        active=True,
        enabled=True,
        visual_ready=True,
        reason="" if db_exists else "Memory database will be created on first use.",
        next_command="openminion graph view --brain second",
        capabilities=("durable_memory", "local_preview", "static_export"),
        details={
            "memory_db": str(db_path),
            "memory_db_exists": db_exists,
            "sample_records": sample_records,
        },
    )


def _memory_db_sample_count(db_path: Path) -> int:
    try:
        from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
        from sophiagraph.query import ListQueryOptions
    except ModuleNotFoundError:
        return 0
    try:
        store = SQLiteMemoryStore(db_path)
        return len(tuple(store.list(ListQueryOptions(limit=20))))
    except (OSError, RuntimeError, ValueError):
        return 0


class OpenMinionMemoryGraphFakosProvider:
    provider_id = _OPENMINION_MEMORY_PROVIDER_ID
    provider_label = "OpenMinion Memory"
    graph_role = "second_brain_memory"
    capabilities = (
        "search",
        "neighborhood",
        "path",
        "provenance",
        "timeline",
        "provider_status",
        "context_preview",
        "durable_memory",
        "static_export",
        "local_preview",
    )

    def __init__(self, *, graphfakos: Any, db_path: Path, limit: int) -> None:
        self._graphfakos = graphfakos
        self._db_path = db_path
        self._limit = limit

    def load_graph(self, request: Any) -> Any:
        from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
        from sophiagraph.query import ListQueryOptions

        store = SQLiteMemoryStore(self._db_path)
        scopes = _scope_filters(request)
        records = tuple(
            store.list(
                ListQueryOptions(
                    scopes=scopes,
                    include_invalidated=True,
                    limit=max(1, int(request.limit or self._limit)),
                )
            )
        )
        record_ids = {record.id for record in records}
        relations = tuple(
            relation
            for record in records
            for relation in store.list_relations(record.id)
            if relation.source_record_id in record_ids
            and relation.target_record_id in record_ids
        )
        unique_relations = {relation.relation_id: relation for relation in relations}
        return self._graphfakos.GraphFakosGraph(
            graph_id=f"openminion-memory:{self._db_path.name}",
            label="OpenMinion Second-Brain Memory",
            provider_id=self.provider_id,
            provider_label=self.provider_label,
            graph_role=self.graph_role,
            capabilities=self.capabilities,
            nodes=tuple(
                _memory_record_node(self._graphfakos, record) for record in records
            ),
            edges=tuple(
                _memory_relation_edge(self._graphfakos, relation)
                for relation in unique_relations.values()
            ),
            provenance=tuple(
                _memory_record_provenance(self._graphfakos, record)
                for record in records
            ),
            citations=tuple(
                citation
                for record in records
                for citation in _memory_record_citations(self._graphfakos, record)
            ),
            warnings=()
            if records
            else (f"No memory records found in {self._db_path}.",),
            stats={
                "db_path": str(self._db_path),
                "records": len(records),
                "relations": len(unique_relations),
                "scope_filter": list(scopes),
            },
            provider_details={
                "layer": LAYER_SECOND_BRAIN,
                "storage": "openminion memory SQLite",
            },
            available_facets={
                "node_kind": tuple(sorted({str(record.type) for record in records})),
                "edge_kind": tuple(
                    sorted(
                        {
                            str(relation.relation_type)
                            for relation in unique_relations.values()
                        }
                    )
                ),
            },
        )


def _scope_filters(request: Any) -> list[str]:
    raw_scope = str(getattr(request, "filters", {}).get("scope", "") or "").strip()
    if raw_scope:
        return [raw_scope]
    return []


def _memory_record_node(graphfakos: Any, record: Any) -> Any:
    provenance_id = f"provenance:{record.id}"
    record_type = str(getattr(record, "type", "") or "memory")
    visual = _memory_record_visual(graphfakos, record_type, record)
    citation_ids = tuple(
        f"citation:{record.id}:{index}"
        for index, _ref in enumerate(getattr(record, "evidence_refs", ()) or ())
    )
    return graphfakos.GraphFakosNode(
        id=record.id,
        label=str(record.title or record.key or record.id),
        kind=record_type,
        summary=_content_summary(record.content),
        tags=_memory_record_tags(record, record_type),
        score=float(getattr(record, "confidence", 0.0) or 0.0),
        confidence=float(getattr(record, "confidence", 0.0) or 0.0),
        source=str(getattr(record, "source", "") or ""),
        timestamps={
            "created_at": str(getattr(record, "created_at", "") or ""),
            "updated_at": str(getattr(record, "updated_at", "") or ""),
        },
        visual=visual,
        provenance_ids=(provenance_id,),
        citation_ids=citation_ids,
        provider_payload={
            "scope": str(getattr(record, "scope", "") or ""),
            "tier": str(getattr(record, "tier", "") or ""),
            "entities": list(getattr(record, "entities", ()) or ()),
            "namespace": record.effective_namespace.as_dict(),
            "memory_type": record_type,
            "confidence": float(getattr(record, "confidence", 0.0) or 0.0),
            "created_at": str(getattr(record, "created_at", "") or ""),
            "updated_at": str(getattr(record, "updated_at", "") or ""),
        },
    )


def _memory_record_visual(graphfakos: Any, record_type: str, record: Any) -> Any:
    template = _MEMORY_TYPE_VISUALS.get(record_type, _DEFAULT_MEMORY_VISUAL)
    confidence = float(getattr(record, "confidence", 0.0) or 0.0)
    return graphfakos.GraphFakosVisual(
        color=template["color"],
        icon=template["icon"],
        shape=template["shape"],
        size=max(1, min(5, int(round(confidence * 4)) + 1)),
        group=record_type,
        emphasis="high_confidence" if confidence >= 0.8 else "",
    )


def _memory_record_tags(record: Any, record_type: str) -> tuple[str, ...]:
    raw_tags = [str(tag) for tag in getattr(record, "tags", ()) or () if str(tag)]
    typed_tags = [
        f"type:{record_type}",
        f"tier:{getattr(record, 'tier', '')}",
        f"scope:{getattr(record, 'scope', '')}",
    ]
    return tuple(dict.fromkeys(tag for tag in (*typed_tags, *raw_tags) if tag))


def _memory_relation_edge(graphfakos: Any, relation: Any) -> Any:
    return graphfakos.GraphFakosEdge(
        id=relation.relation_id,
        source_id=relation.source_record_id,
        target_id=relation.target_record_id,
        kind=str(relation.relation_type),
        label=str(relation.relation_type).replace("_", " "),
        provider_payload={
            "created_at": relation.created_at,
            "meta": dict(relation.meta),
        },
    )


def _memory_record_provenance(graphfakos: Any, record: Any) -> Any:
    return graphfakos.GraphFakosProvenance(
        id=f"provenance:{record.id}",
        provider_id="openminion-memory",
        source_type=str(getattr(record, "source", "") or "memory"),
        source_label=str(record.title or record.key or record.id),
        excerpt=_content_summary(record.content),
        created_at=str(getattr(record, "created_at", "") or ""),
        updated_at=str(getattr(record, "updated_at", "") or ""),
        confidence=float(getattr(record, "confidence", 0.0) or 0.0),
    )


def _memory_record_citations(graphfakos: Any, record: Any) -> tuple[Any, ...]:
    citations = []
    for index, ref in enumerate(getattr(record, "evidence_refs", ()) or ()):
        citations.append(
            graphfakos.GraphFakosCitation(
                id=f"citation:{record.id}:{index}",
                label=str(
                    getattr(ref, "label", "")
                    or getattr(ref, "source_id", "")
                    or record.id
                ),
                uri=str(getattr(ref, "uri", "") or ""),
                path=str(getattr(ref, "path", "") or ""),
                excerpt=str(getattr(ref, "quote", "") or ""),
                provider_payload={
                    "record_id": record.id,
                    "source_id": str(getattr(ref, "source_id", "") or ""),
                },
            )
        )
    return tuple(citations)


def _content_summary(content: object) -> str:
    if isinstance(content, str):
        return content[:500]
    if isinstance(content, Mapping):
        for key in ("text", "summary", "body", "value"):
            value = content.get(key)
            if value:
                return str(value)[:500]
    return str(content)[:500]


def _third_brain_provider_config(
    config: OpenMinionConfig,
    request: GraphViewerRequest,
) -> KnowledgeGraphProviderConfig:
    graph_config = resolve_knowledge_graphs_config(config)
    layer_config = graph_config.provider
    provider_name = request.provider.strip() or _single_active_provider(layer_config)
    provider_config = layer_config.providers.get(provider_name)
    if provider_config is None:
        raise UnknownProviderError(
            f"No active third-brain graph provider named {provider_name!r}",
            details={
                "provider": provider_name,
                "active": list(layer_config.active),
                "configured": sorted(layer_config.providers),
            },
        )
    return provider_config


def _single_active_provider(layer_config: KnowledgeGraphLayerConfig) -> str:
    if len(layer_config.active) == 1:
        return layer_config.active[0]
    if not layer_config.active:
        raise UnknownProviderError(
            "No active third-brain graph provider is configured.",
            details={"active": []},
        )
    raise UnknownProviderError(
        "Multiple third-brain providers are active; pass --provider.",
        details={
            "active": list(layer_config.active),
            "suggested_commands": [
                f"openminion graph view --brain third --provider {provider_name}"
                for provider_name in layer_config.active
            ],
        },
    )


def _third_brain_graphfakos_provider(
    *,
    graphfakos: Any,
    provider_config: KnowledgeGraphProviderConfig,
    roots: CLIRoots,
    request: GraphViewerRequest,
) -> Any:
    options = dict(provider_config.options or {})
    envelope_path = _option_path(options.get(_VIEWER_ENVELOPE_PATH_OPTION), roots=roots)
    if envelope_path is not None:
        return graphfakos.ProviderEnvelopeGraphProvider(str(envelope_path))
    if provider_config.provider == PROVIDER_PRAGMAGRAPH:
        return _pragmagraph_envelope_provider(
            graphfakos=graphfakos,
            provider_config=provider_config,
            roots=roots,
            request=request,
        )
    raise GraphViewerSourceError(
        "This provider does not expose a GraphFakos viewer envelope yet. "
        f"Add options.{_VIEWER_ENVELOPE_PATH_OPTION} to its knowledge_graphs config.",
        details={
            "provider": provider_config.name,
            "adapter": provider_config.provider,
            "option": _VIEWER_ENVELOPE_PATH_OPTION,
            "suggested_command": (
                f"openminion graph status --provider {provider_config.name}"
            ),
        },
    )


def _third_brain_statuses(
    *,
    config: OpenMinionConfig,
    roots: CLIRoots,
    selected_provider: str,
    graphfakos_installed: bool,
) -> tuple[GraphViewerProviderStatus, ...]:
    graph_config = resolve_knowledge_graphs_config(config)
    layer_config = graph_config.provider
    selected = selected_provider.strip()
    names = (selected,) if selected else tuple(layer_config.providers)
    statuses = []
    for name in names:
        provider_config = layer_config.providers.get(name)
        if provider_config is None:
            statuses.append(
                GraphViewerProviderStatus(
                    provider=name,
                    layer=LAYER_THIRD_BRAIN,
                    adapter="",
                    active=name in layer_config.active,
                    enabled=False,
                    visual_ready=False,
                    reason="Provider is not configured.",
                    next_command="openminion graph status",
                    details={
                        "active": list(layer_config.active),
                        "configured": sorted(layer_config.providers),
                    },
                )
            )
            continue
        statuses.append(
            _third_brain_provider_status(
                provider_config=provider_config,
                active=provider_config.name in layer_config.active,
                roots=roots,
                graphfakos_installed=graphfakos_installed,
            )
        )
    return tuple(statuses)


def _third_brain_provider_status(
    *,
    provider_config: KnowledgeGraphProviderConfig,
    active: bool,
    roots: CLIRoots,
    graphfakos_installed: bool,
) -> GraphViewerProviderStatus:
    options = dict(provider_config.options or {})
    envelope_path = _option_path(options.get(_VIEWER_ENVELOPE_PATH_OPTION), roots=roots)
    snapshot_path = _option_path(options.get("snapshot_path"), roots=roots)
    capabilities = tuple(
        dict.fromkeys(
            (*provider_config.required_capabilities, *provider_config.optional_capabilities)
        )
    )
    ready = bool(
        graphfakos_installed
        and provider_config.enabled
        and (
            envelope_path is not None
            or (provider_config.provider == PROVIDER_PRAGMAGRAPH and snapshot_path is not None)
        )
    )
    reason = _third_brain_status_reason(
        provider_config=provider_config,
        graphfakos_installed=graphfakos_installed,
        envelope_path=envelope_path,
        snapshot_path=snapshot_path,
    )
    command = (
        f"openminion graph view --brain third --provider {provider_config.name}"
        if ready
        else "openminion graph status"
    )
    return GraphViewerProviderStatus(
        provider=provider_config.name,
        layer=LAYER_THIRD_BRAIN,
        adapter=provider_config.provider,
        active=active,
        enabled=provider_config.enabled,
        visual_ready=ready,
        reason=reason,
        next_command=command,
        tags=provider_config.tags,
        capabilities=capabilities,
        details={
            "viewer_envelope_path": str(envelope_path) if envelope_path else "",
            "viewer_envelope_exists": bool(envelope_path and envelope_path.exists()),
            "snapshot_path": str(snapshot_path) if snapshot_path else "",
            "snapshot_exists": bool(snapshot_path and snapshot_path.exists()),
            "refresh_mode": provider_config.refresh.mode,
        },
    )


def _third_brain_status_reason(
    *,
    provider_config: KnowledgeGraphProviderConfig,
    graphfakos_installed: bool,
    envelope_path: Path | None,
    snapshot_path: Path | None,
) -> str:
    if not graphfakos_installed:
        return "GraphFakos is not installed."
    if not provider_config.enabled:
        return "Provider is disabled."
    if envelope_path is not None:
        return "" if envelope_path.exists() else "Viewer envelope path is configured but not found yet."
    if provider_config.provider == PROVIDER_PRAGMAGRAPH and snapshot_path is not None:
        return "" if snapshot_path.exists() else "PragmaGraph snapshot path is configured but not found yet."
    if provider_config.provider == PROVIDER_PRAGMAGRAPH:
        return "PragmaGraph viewer needs options.snapshot_path or options.viewer_envelope_path."
    return f"Provider needs options.{_VIEWER_ENVELOPE_PATH_OPTION} for visual inspection."


def _status_next_commands(
    *,
    graphfakos_installed: bool,
    second_brain: GraphViewerProviderStatus,
    third_brain: tuple[GraphViewerProviderStatus, ...],
) -> tuple[str, ...]:
    commands = []
    if not graphfakos_installed:
        commands.append("python -m pip install 'openminion[viewer]'")
    if second_brain.visual_ready:
        commands.append(second_brain.next_command)
    commands.extend(
        provider.next_command
        for provider in third_brain
        if provider.visual_ready and provider.active
    )
    return tuple(dict.fromkeys(command for command in commands if command))


def _pragmagraph_envelope_provider(
    *,
    graphfakos: Any,
    provider_config: KnowledgeGraphProviderConfig,
    roots: CLIRoots,
    request: GraphViewerRequest,
) -> Any:
    snapshot_path = _option_path(provider_config.options.get("snapshot_path"), roots=roots)
    if snapshot_path is None:
        raise GraphViewerSourceError(
            "PragmaGraph viewer needs options.snapshot_path or "
            f"options.{_VIEWER_ENVELOPE_PATH_OPTION}.",
            details={
                "provider": provider_config.name,
                "suggested_command": (
                    f"openminion graph status --provider {provider_config.name}"
                ),
            },
        )
    try:
        from pragmagraph.storage import load_snapshot
        from pragmagraph.viewer import build_viewer_envelope
    except ModuleNotFoundError as exc:
        raise GraphViewerUnavailableError(
            "PragmaGraph viewer export requires the pragmagraph package.",
            details={"package": "pragmagraph"},
        ) from exc
    envelope = build_viewer_envelope(
        load_snapshot(snapshot_path),
        node_budget=max(1, int(request.render_limit)),
        edge_budget=max(1, int(request.render_limit * 2)),
    )
    return _InMemoryEnvelopeProvider(graphfakos=graphfakos, envelope=envelope.to_dict())


class _InMemoryEnvelopeProvider:
    provider_id = "pragmagraph"
    provider_label = "PragmaGraph"
    graph_role = "provider_viewer_envelope"
    capabilities = (
        "cluster_overview",
        "large_graph_lod",
        "content_preview",
        "evidence",
        "static_export",
        "local_preview",
    )

    def __init__(self, *, graphfakos: Any, envelope: Mapping[str, object]) -> None:
        self._graphfakos = graphfakos
        self._envelope = dict(envelope)

    def load_graph(self, request: Any) -> Any:
        del request
        return self._graphfakos.graph_from_provider_envelope(
            self._envelope,
            source_path="openminion:knowledge_graphs",
        )


def _option_path(value: object, *, roots: CLIRoots) -> Path | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = roots.home_root / candidate
    return candidate.resolve(strict=False)


def _write_static_html(
    *,
    graphfakos: Any,
    provider: Any,
    graph_request: Any,
    html_out: str,
    roots: CLIRoots,
) -> Path:
    target = Path(html_out).expanduser()
    if not target.is_absolute():
        target = roots.home_root / target
    target = target.resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        graphfakos.render_static_html(provider, graph_request),
        encoding="utf-8",
    )
    return target


def _serve_viewer(
    *,
    graphfakos: Any,
    provider: Any,
    graph_request: Any,
    request: GraphViewerRequest,
) -> Any:
    from graphfakos.preview import LocalPreviewProviderSession
    from graphfakos.ui import render_provider_path, render_provider_path_fragment

    preview_provider = LocalPreviewProviderSession(provider)
    return graphfakos.serve_local_viewer(
        render_path=lambda path, query: render_provider_path(
            preview_provider,
            graph_request,
            path,
            query,
        ),
        render_fragment_path=lambda path, query: render_provider_path_fragment(
            preview_provider,
            graph_request,
            path,
            query,
        ),
        default_path=f"/{graph_request.screen}",
        host=request.host,
        port=request.port,
        open_browser=request.open_browser,
    )


__all__ = [
    "GraphViewerLaunchResult",
    "GraphViewerProviderStatus",
    "GraphViewerRequest",
    "GraphViewerStatusReport",
    "OpenMinionMemoryGraphFakosProvider",
    "inspect_graph_viewer_status",
    "launch_graph_viewer",
]
