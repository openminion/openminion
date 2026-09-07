"""SophiaGraph workspace adapter for an Obsidian-compatible Markdown vault."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any, NoReturn

from ..config import KnowledgeGraphProviderConfig
from ..constants import (
    CAPABILITY_CITATIONS,
    CAPABILITY_NEIGHBORHOOD,
    CAPABILITY_PROVENANCE,
    CAPABILITY_QUERY,
    CAPABILITY_REFRESH,
    LAYER_THIRD_BRAIN,
    SOPHIAGRAPH_WORKSPACE_OPTION_SOURCE_ROOT,
    SOPHIAGRAPH_WORKSPACE_OPTION_WORKSPACE_ROOT,
    TAG_DOCUMENT_GRAPH,
)
from ..errors import KnowledgeGraphError, UnsupportedCapabilityError
from ..interfaces import KNOWLEDGE_GRAPH_INTERFACE_VERSION
from ..models import (
    GraphContextItem,
    GraphExplainRequest,
    GraphExplainResult,
    GraphNeighborhoodRequest,
    GraphPathEvidence,
    GraphPathRequest,
    GraphPathResult,
    GraphQueryRequest,
    GraphQueryResult,
    GraphRefreshRequest,
    GraphRefreshResult,
    GraphSourceRef,
    KnowledgeGraphCapabilities,
    KnowledgeGraphHealth,
)


class SophiagraphWorkspaceKnowledgeGraphSource:
    """Expose one initialized SophiaGraph workspace through graph DTOs."""

    contract_version = KNOWLEDGE_GRAPH_INTERFACE_VERSION

    def __init__(
        self,
        *,
        config: KnowledgeGraphProviderConfig,
        layer: str = LAYER_THIRD_BRAIN,
    ) -> None:
        self._config = config
        self._layer = layer
        self._workspace_root = _required_path(
            config, SOPHIAGRAPH_WORKSPACE_OPTION_WORKSPACE_ROOT
        )
        self._source_root = _required_path(
            config, SOPHIAGRAPH_WORKSPACE_OPTION_SOURCE_ROOT
        )

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def layer(self) -> str:
        return self._layer

    @property
    def tags(self) -> tuple[str, ...]:
        return self._config.tags or (TAG_DOCUMENT_GRAPH,)

    @property
    def capabilities(self) -> KnowledgeGraphCapabilities:
        return KnowledgeGraphCapabilities(
            advertised=frozenset(
                {
                    CAPABILITY_QUERY,
                    CAPABILITY_NEIGHBORHOOD,
                    CAPABILITY_REFRESH,
                    CAPABILITY_CITATIONS,
                    CAPABILITY_PROVENANCE,
                }
            )
        )

    def health(self) -> KnowledgeGraphHealth:
        try:
            sophia, status, _store, upstream_error = self._workspace()
            try:
                sync = sophia.workspace_sync_status(
                    self._workspace_root, self._source_root
                )
            except upstream_error as exc:
                self._raise_workspace_error(exc)
        except KnowledgeGraphError as exc:
            return KnowledgeGraphHealth(
                provider=self.name,
                layer=self.layer,
                ok=False,
                detail=exc.code,
                diagnostics={"reason_code": exc.details.get("reason_code", "")},
            )
        return KnowledgeGraphHealth(
            provider=self.name,
            layer=self.layer,
            ok=True,
            detail="ready",
            diagnostics={
                "graph_id": status.import_profile.vault_id,
                "scope": status.metadata.scope,
                "record_count": status.record_count,
                "pending_count": int(sync.pending_delta_count),
                "package_version": str(getattr(sophia, "__version__", "") or ""),
            },
        )

    def query(self, request: GraphQueryRequest) -> GraphQueryResult:
        sophia, status, store, upstream_error = self._workspace()
        limit = request.max_results or self._config.retrieval.max_results
        try:
            records = store.search_records(
                sophia.SearchQueryOptions(
                    query=request.query,
                    scopes=[status.metadata.scope],
                    namespaces=[status.metadata.namespace],
                    limit=limit,
                )
            )
        except upstream_error as exc:
            self._raise_workspace_error(exc)
        items = tuple(
            self._record_item(record, status.import_profile.vault_id, request.max_chars)
            for record in records
            if _vault_id(record) == status.import_profile.vault_id
        )
        return GraphQueryResult(
            provider=self.name,
            layer=self.layer,
            tags=self.tags,
            items=items,
            diagnostics={"graph_id": status.import_profile.vault_id},
        )

    def neighborhood(self, request: GraphNeighborhoodRequest) -> GraphQueryResult:
        sophia, status, store, upstream_error = self._workspace()
        limit = request.max_results or self._config.retrieval.max_results
        try:
            graph = store.get_local_graph(
                sophia.LocalGraphOptions(
                    record_id=request.entity_id,
                    depth=request.depth,
                    direction="both",
                    namespaces=[status.metadata.namespace],
                    max_nodes=limit,
                    max_edges=max(limit, limit * 2),
                )
            )
        except upstream_error as exc:
            self._raise_workspace_error(exc)
        graph_id = status.import_profile.vault_id
        items = tuple(self._node_item(node, graph_id) for node in graph.nodes)
        edges = tuple(_edge_mapping(edge) for edge in graph.edges)
        paths = (
            (GraphPathEvidence(provider=self.name, nodes=items, edges=edges),)
            if items or edges
            else ()
        )
        return GraphQueryResult(
            provider=self.name,
            layer=self.layer,
            tags=self.tags,
            items=items,
            paths=paths,
            diagnostics={"graph_id": graph_id},
        )

    def refresh(self, request: GraphRefreshRequest) -> GraphRefreshResult:
        sophia, status, _store, upstream_error = self._workspace()
        try:
            plan = sophia.scan_workspace_sync(self._workspace_root, self._source_root)
            result = sophia.apply_workspace_sync(
                self._workspace_root,
                self._source_root,
                plan=plan,
            )
        except upstream_error as exc:
            self._raise_workspace_error(exc)
        return GraphRefreshResult(
            provider=self.name,
            layer=self.layer,
            ok=True,
            refreshed_at=str(getattr(result, "applied_at", "") or ""),
            counts={
                "changed": len(tuple(plan.deltas)),
                "imported": len(tuple(getattr(result, "imported_paths", ()) or ())),
                "stale": len(tuple(getattr(result, "stale_paths", ()) or ())),
                "conflicts": len(tuple(getattr(result, "conflict_ids", ()) or ())),
            },
            diagnostics={
                "mode": request.mode,
                "graph_id": status.import_profile.vault_id,
            },
        )

    def path(self, request: GraphPathRequest) -> GraphPathResult:
        del request
        raise UnsupportedCapabilityError(
            "SophiaGraph workspace adapter does not advertise path queries",
            details={"provider": self.name, "capability": "path"},
        )

    def explain(self, request: GraphExplainRequest) -> GraphExplainResult:
        del request
        raise UnsupportedCapabilityError(
            "SophiaGraph workspace adapter does not advertise explanations",
            details={"provider": self.name, "capability": "explain"},
        )

    def _workspace(self) -> tuple[Any, Any, Any, type[Exception]]:
        try:
            sophia = import_module("sophiagraph")
        except ImportError as exc:
            raise KnowledgeGraphError(
                "SophiaGraph package is unavailable",
                details={
                    "provider": self.name,
                    "reason_code": "package_unavailable",
                },
            ) from exc
        errors = import_module("sophiagraph.contracts.errors")
        try:
            status = sophia.load_workspace_status(self._workspace_root)
            store = sophia.open_workspace_store(self._workspace_root)
        except errors.MemctlError as exc:
            raise KnowledgeGraphError(
                "SophiaGraph workspace is unavailable",
                details={
                    "provider": self.name,
                    "reason_code": "workspace_unavailable",
                    "upstream_code": exc.code,
                },
            ) from exc
        return sophia, status, store, errors.MemctlError

    def _raise_workspace_error(self, exc: Any) -> NoReturn:
        raise KnowledgeGraphError(
            "SophiaGraph workspace operation failed",
            details={
                "provider": self.name,
                "reason_code": "workspace_unavailable",
                "upstream_code": str(getattr(exc, "code", "UPSTREAM_ERROR")),
            },
        ) from exc

    def _record_item(
        self, record: Any, graph_id: str, max_chars: int | None
    ) -> GraphContextItem:
        vault = _vault_meta(record)
        content = getattr(record, "content", {}) or {}
        text = str(content.get("text", "") if isinstance(content, dict) else content)
        budget = max_chars or self._config.retrieval.max_chars
        return GraphContextItem(
            provider=self.name,
            source_graph_id=graph_id,
            node_or_edge_id=str(record.id),
            source_ref=GraphSourceRef(path=str(vault.get("path", "") or "")),
            snippet=text[:budget],
            score=getattr(record, "confidence", None),
            metadata={
                "kind": str(getattr(record, "type", "") or "note"),
                "title": str(getattr(record, "title", "") or ""),
                "tags": list(getattr(record, "tags", ()) or ()),
            },
        )

    def _node_item(self, node: Any, graph_id: str) -> GraphContextItem:
        return GraphContextItem(
            provider=self.name,
            source_graph_id=graph_id,
            node_or_edge_id=str(node.record_id),
            source_ref=GraphSourceRef(path=str(node.path or "")),
            snippet=str(node.title or ""),
            metadata={
                "kind": "note",
                "title": str(node.title or ""),
                "tags": list(node.tags or ()),
                "degree_in": node.degree_in,
                "degree_out": node.degree_out,
                "orphan": node.orphan,
            },
        )


def _required_path(config: KnowledgeGraphProviderConfig, key: str) -> Path:
    value = str(config.options.get(key, "") or "").strip()
    if value:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
    raise KnowledgeGraphError(
        f"Knowledge-graph provider option {key!r} must be an absolute path",
        details={
            "provider": config.name,
            "reason_code": "configuration_invalid",
            "field": key,
        },
    )


def _vault_meta(record: Any) -> dict[str, Any]:
    meta = getattr(record, "meta", {}) or {}
    vault = meta.get("vault", {}) if isinstance(meta, dict) else {}
    return dict(vault) if isinstance(vault, dict) else {}


def _vault_id(record: Any) -> str:
    return str(_vault_meta(record).get("vault_id", "") or "")


def _edge_mapping(edge: Any) -> dict[str, Any]:
    return {
        "edge_id": str(edge.edge_id),
        "source_record_id": str(edge.source_record_id),
        "target_record_id": str(edge.target_record_id or ""),
        "relation_type": str(edge.relation_type or ""),
        "direction": str(edge.direction or ""),
        "unresolved_target": str(edge.unresolved_target or ""),
        "label": str(edge.label or ""),
    }


__all__ = ["SophiagraphWorkspaceKnowledgeGraphSource"]
