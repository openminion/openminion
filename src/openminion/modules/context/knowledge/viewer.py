"""GraphFakos-backed viewer helpers for OpenMinion graph state."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Mapping, cast

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
from openminion.modules.context.knowledge.viewer_models import (
    GraphViewerLaunchResult,
    GraphViewerProviderStatus,
    GraphViewerRequest,
    GraphViewerStatusReport,
)
from openminion.modules.context.knowledge.viewer_memory import (
    OPENMINION_MEMORY_PROVIDER_ID,
    OpenMinionMemoryGraphFakosProvider,
    memory_db_sample_count,
)
from openminion.modules.memory.constants import DEFAULT_INTEGRATED_SQLITE_SUBPATH

_VIEWER_ENVELOPE_PATH_OPTION = "viewer_envelope_path"


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
    layer = _request_layer(request)
    if request.dry_run:
        graph = provider.load_graph(graph_request)
        return GraphViewerLaunchResult(
            provider=provider.provider_id,
            layer=layer,
            graph_role=provider.graph_role,
            mode="dry_run",
            diagnostics=_graph_diagnostics(graph, graph_request),
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
        filters=_request_filters(request),
        evidence_filter=request.evidence_filter,
    )


def _graph_diagnostics(graph: Any, request: Any) -> dict[str, object]:
    diagnostics: dict[str, object] = {
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "screen": request.screen,
    }
    filters = dict(getattr(request, "filters", {}) or {})
    warnings = tuple(str(item) for item in getattr(graph, "warnings", ()) or ())
    stats = dict(getattr(graph, "stats", {}) or {})
    evidence_filter = str(getattr(request, "evidence_filter", "") or "").strip()
    if filters:
        diagnostics["filters"] = filters
    if evidence_filter:
        diagnostics["evidence_filter"] = evidence_filter
    if warnings:
        diagnostics["warnings"] = list(warnings)
    if stats:
        diagnostics["stats"] = stats
    return diagnostics


def _request_filters(request: GraphViewerRequest) -> dict[str, str]:
    filters = {
        "node_kind": request.node_kind,
        "edge_kind": request.edge_kind,
        "tag": request.tag,
        "source": request.source,
        "min_score": request.min_score,
    }
    scopes = _scope_values(request)
    if scopes:
        filters["scope"] = ",".join(scopes)
    return {
        key: str(value).strip() for key, value in filters.items() if str(value).strip()
    }


def _viewer_provider(
    *,
    graphfakos: Any,
    config: OpenMinionConfig,
    roots: CLIRoots,
    request: GraphViewerRequest,
) -> Any:
    if _request_layer(request) == LAYER_SECOND_BRAIN:
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


def _request_layer(request: GraphViewerRequest) -> str:
    if request.current:
        return LAYER_SECOND_BRAIN
    return _layer_from_brain(request.brain)


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
    sample_records = memory_db_sample_count(db_path) if db_exists else 0
    current_command = "openminion graph view --current"
    if not graphfakos_installed:
        return GraphViewerProviderStatus(
            provider=OPENMINION_MEMORY_PROVIDER_ID,
            layer=LAYER_SECOND_BRAIN,
            adapter="sophiagraph-sqlite",
            active=True,
            enabled=True,
            visual_ready=False,
            reason="GraphFakos is not installed.",
            next_command="python -m pip install 'openminion[viewer]'",
            capabilities=("durable_memory", "local_preview", "static_export"),
            details={
                "diagnostic_code": "graphfakos_missing",
                "diagnostic_label": _diagnostic_label("graphfakos_missing"),
                "memory_db": str(db_path),
                "memory_db_exists": db_exists,
                "sample_records": sample_records,
                "status_command": "openminion graph status",
            },
        )
    return GraphViewerProviderStatus(
        provider=OPENMINION_MEMORY_PROVIDER_ID,
        layer=LAYER_SECOND_BRAIN,
        adapter="sophiagraph-sqlite",
        active=True,
        enabled=True,
        visual_ready=True,
        reason="" if db_exists else "Memory database will be created on first use.",
        next_command=current_command,
        capabilities=("durable_memory", "local_preview", "static_export"),
        details={
            "diagnostic_code": "ready" if db_exists else "memory_db_missing",
            "diagnostic_label": _diagnostic_label(
                "ready" if db_exists else "memory_db_missing"
            ),
            "memory_db": str(db_path),
            "memory_db_exists": db_exists,
            "sample_records": sample_records,
            "status_command": "openminion graph status",
            "current_command": current_command,
            "scoped_commands": [
                f"{current_command} --agent <agent-id>",
                f"{current_command} --session <session-id>",
                f"{current_command} --node-kind fact",
                f"{current_command} --tag scope:<scope>",
            ],
        },
    )


def _scope_values(request: GraphViewerRequest) -> tuple[str, ...]:
    scopes = []
    session_id = str(request.session_id or "").strip()
    agent_id = str(request.agent_id or "").strip()
    if session_id:
        scopes.append(f"session:{session_id}")
    if agent_id:
        scopes.append(f"agent:{agent_id}")
    return tuple(dict.fromkeys(scopes))


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
            (
                *provider_config.required_capabilities,
                *provider_config.optional_capabilities,
            )
        )
    )
    envelope_ready = bool(envelope_path and envelope_path.exists())
    snapshot_ready = bool(
        provider_config.provider == PROVIDER_PRAGMAGRAPH
        and snapshot_path
        and snapshot_path.exists()
    )
    ready = bool(
        graphfakos_installed
        and provider_config.enabled
        and (envelope_ready or snapshot_ready)
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
    diagnostic_code = _third_brain_diagnostic_code(
        provider_config=provider_config,
        graphfakos_installed=graphfakos_installed,
        envelope_path=envelope_path,
        snapshot_path=snapshot_path,
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
            "diagnostic_code": diagnostic_code,
            "diagnostic_label": _diagnostic_label(diagnostic_code),
            "viewer_envelope_path": str(envelope_path) if envelope_path else "",
            "viewer_envelope_exists": bool(envelope_path and envelope_path.exists()),
            "snapshot_path": str(snapshot_path) if snapshot_path else "",
            "snapshot_exists": bool(snapshot_path and snapshot_path.exists()),
            "refresh_mode": provider_config.refresh.mode,
            "status_command": f"openminion graph status --provider {provider_config.name}",
            "view_command": (
                f"openminion graph view --brain third --provider {provider_config.name}"
            ),
            "suggested_config": (
                f"knowledge_graphs.provider.providers.{provider_config.name}."
                f"options.{_VIEWER_ENVELOPE_PATH_OPTION}"
            ),
        },
    )


def _third_brain_diagnostic_code(
    *,
    provider_config: KnowledgeGraphProviderConfig,
    graphfakos_installed: bool,
    envelope_path: Path | None,
    snapshot_path: Path | None,
) -> str:
    if not graphfakos_installed:
        return "graphfakos_missing"
    if not provider_config.enabled:
        return "provider_disabled"
    if envelope_path is not None:
        return "ready" if envelope_path.exists() else "viewer_envelope_missing"
    if provider_config.provider == PROVIDER_PRAGMAGRAPH and snapshot_path is not None:
        return "ready" if snapshot_path.exists() else "snapshot_missing"
    return "viewer_envelope_unconfigured"


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
        return (
            ""
            if envelope_path.exists()
            else "Viewer envelope path is configured but not found yet."
        )
    if provider_config.provider == PROVIDER_PRAGMAGRAPH and snapshot_path is not None:
        return (
            ""
            if snapshot_path.exists()
            else "PragmaGraph snapshot path is configured but not found yet."
        )
    if provider_config.provider == PROVIDER_PRAGMAGRAPH:
        return "PragmaGraph viewer needs options.snapshot_path or options.viewer_envelope_path."
    return (
        f"Provider needs options.{_VIEWER_ENVELOPE_PATH_OPTION} for visual inspection."
    )


def _diagnostic_label(code: str) -> str:
    return {
        "graphfakos_missing": "Install the viewer extra before opening graphs.",
        "memory_db_missing": "Memory graph is ready; the database appears after first use.",
        "provider_disabled": "Enable the provider before opening it.",
        "ready": "Ready to open visually.",
        "snapshot_missing": "Configured PragmaGraph snapshot was not found.",
        "viewer_envelope_missing": "Configured viewer envelope was not found.",
        "viewer_envelope_unconfigured": "Configure a viewer envelope or supported snapshot.",
    }.get(code, "Check graph status for details.")


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
    snapshot_path = _option_path(
        provider_config.options.get("snapshot_path"), roots=roots
    )
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
    preview_graph_provider = cast(Any, preview_provider)
    return graphfakos.serve_local_viewer(
        render_path=lambda path, query: render_provider_path(
            preview_graph_provider,
            graph_request,
            path,
            query,
        ),
        render_fragment_path=lambda path, query: render_provider_path_fragment(
            preview_graph_provider,
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
