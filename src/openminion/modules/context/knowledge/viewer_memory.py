"""Second-brain memory provider adapter for the GraphFakos viewer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from openminion.modules.context.knowledge.constants import LAYER_SECOND_BRAIN

OPENMINION_MEMORY_PROVIDER_ID = "openminion-memory"

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


def memory_db_sample_count(db_path: Path) -> int:
    try:
        from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
        from sophiagraph.query import ListQueryOptions
    except ModuleNotFoundError:
        return 0
    try:
        store = SQLiteMemoryStore(db_path)
        return len(tuple(store.list(ListQueryOptions(scopes=[], limit=20))))
    except (OSError, RuntimeError, ValueError):
        return 0


class OpenMinionMemoryGraphFakosProvider:
    provider_id = OPENMINION_MEMORY_PROVIDER_ID
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
        "live_refresh",
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
        limit = max(1, int(request.limit or self._limit))
        query_limit = None if _has_post_query_filters(request) else limit
        records = tuple(
            record
            for record in store.list(
                ListQueryOptions(
                    scopes=scopes,
                    include_invalidated=True,
                    limit=query_limit,
                )
            )
            if _record_matches_filters(record, request)
        )[:limit]
        record_ids = {record.id for record in records}
        edge_kind = _filter_value(request, "edge_kind")
        relations: list[Any] = []
        for record in records:
            record_relations: tuple[Any, ...] = tuple(
                store.list_relations(record.id) or ()
            )
            for relation in record_relations:
                if (
                    relation.source_record_id in record_ids
                    and relation.target_record_id in record_ids
                    and _relation_matches_edge_kind(relation, edge_kind)
                ):
                    relations.append(relation)
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
            else (
                "No second-brain memory records matched this view. "
                "The viewer did not write sample data.",
            ),
            stats=_memory_graph_stats(self._db_path, records, unique_relations, scopes),
            provider_details=_memory_provider_details(),
            provider_payload=_memory_provider_payload(records),
            available_facets=_memory_available_facets(records, unique_relations),
        )


def _scope_filters(request: Any) -> list[str]:
    raw_scope = str(getattr(request, "filters", {}).get("scope", "") or "").strip()
    if raw_scope:
        return [scope for scope in _split_scope_filter(raw_scope) if scope]
    return []


def _record_matches_filters(record: Any, request: Any) -> bool:
    record_type = str(getattr(record, "type", "") or "")
    query = str(getattr(request, "query", "") or "").strip()
    if query and not _record_matches_query(record, query):
        return False
    node_kind = _filter_value(request, "node_kind")
    if node_kind and record_type != node_kind:
        return False
    tag = _filter_value(request, "tag")
    if tag and tag not in _memory_record_tags(record, record_type):
        return False
    source = _filter_value(request, "source")
    if source and str(getattr(record, "source", "") or "") != source:
        return False
    min_score = _min_score_filter(request)
    confidence = float(getattr(record, "confidence", 0.0) or 0.0)
    return min_score is None or confidence >= min_score


def _relation_matches_edge_kind(relation: Any, edge_kind: str) -> bool:
    return (
        not edge_kind
        or str(getattr(relation, "relation_type", "") or "") == edge_kind
    )


def _has_post_query_filters(request: Any) -> bool:
    return bool(
        str(getattr(request, "query", "") or "").strip()
        or _filter_value(request, "node_kind")
        or _filter_value(request, "tag")
        or _filter_value(request, "source")
        or _filter_value(request, "min_score")
    )


def _record_matches_query(record: Any, query: str) -> bool:
    needle = query.casefold()
    return any(needle in value.casefold() for value in _record_search_values(record))


def _record_search_values(record: Any) -> tuple[str, ...]:
    content = getattr(record, "content", "")
    if isinstance(content, Mapping):
        content_values = tuple(str(value) for value in content.values() if value)
    else:
        content_values = (str(content),)
    return (
        str(getattr(record, "id", "") or ""),
        str(getattr(record, "key", "") or ""),
        str(getattr(record, "title", "") or ""),
        *content_values,
    )


def _filter_value(request: Any, key: str) -> str:
    filters = getattr(request, "filters", {}) or {}
    return str(filters.get(key, "") or "").strip()


def _min_score_filter(request: Any) -> float | None:
    raw = _filter_value(request, "min_score")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _split_scope_filter(raw_scope: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(item.strip() for item in raw_scope.split(",") if item.strip())
    )


def _sorted_unique(values: Any) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if str(value)}))


def _memory_graph_stats(
    db_path: Path,
    records: tuple[Any, ...],
    relations: Mapping[str, Any],
    scopes: list[str],
) -> dict[str, object]:
    return {
        "db_path": str(db_path),
        "records": len(records),
        "relations": len(relations),
        "scope_filter": list(scopes),
        "empty_code": "current_memory_empty" if not records else "",
        "memory_types": _sorted_unique(str(record.type) for record in records),
        "tiers": _sorted_unique(
            str(getattr(record, "tier", "") or "") for record in records
        ),
    }


def _memory_provider_details() -> dict[str, str]:
    return {
        "layer": LAYER_SECOND_BRAIN,
        "owner": "OpenMinion memory",
        "storage": "openminion memory SQLite",
        "refresh_strategy": "live_snapshot_reset_or_requery",
        "mutation_policy": "read_only_viewer",
        "filterable_fields": ",".join(
            (
                "query",
                "node_kind",
                "edge_kind",
                "tag",
                "source",
                "min_score",
                "evidence_filter",
            )
        ),
    }


def _memory_provider_payload(records: tuple[Any, ...]) -> dict[str, object]:
    return {
        "empty_state": _memory_empty_state() if not records else {},
        "refresh": {
            "strategy": "live_snapshot_reset_or_requery",
            "writes_memory": False,
            "live_patch_stream": True,
        },
        "mutation_policy": {
            "durable_memory_writes": "unsupported_from_viewer",
            "knowledge_capture": "provider_owned_when_supported",
            "graph_actions": "provider_owned_when_supported",
        },
        "local_endpoints": {
            "graph_action": "/api/action",
            "knowledge_capture": "/api/knowledge",
            "import_graph": "/api/import",
            "reset_preview": "/api/reset",
        },
        "viewer_actions": (
            "search",
            "filter",
            "inspect_node",
            "focus_neighborhood",
            "highlight_path",
            "show_provenance",
            "copy_citation",
            "export_visible_graph",
            "open_provider_status",
        ),
    }


def _memory_empty_state() -> dict[str, str]:
    return {
        "code": "current_memory_empty",
        "message": (
            "No second-brain memory records matched this view. "
            "No sample data was written."
        ),
    }


def _memory_available_facets(
    records: tuple[Any, ...],
    relations: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    return {
        "node_kind": _sorted_unique(str(record.type) for record in records),
        "edge_kind": _sorted_unique(
            str(relation.relation_type) for relation in relations.values()
        ),
        "source": _sorted_unique(
            str(getattr(record, "source", "") or "") for record in records
        ),
        "tag": _sorted_unique(
            tag
            for record in records
            for tag in _memory_record_tags(record, str(record.type))
        ),
    }


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
        provider_id=OPENMINION_MEMORY_PROVIDER_ID,
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


class OpenMinionMemoryGraphFakosLiveProvider:
    """Expose current memory graph changes through GraphFakos live patches."""

    def __init__(self, *, graphfakos: Any, provider: Any, request: Any) -> None:
        self._graphfakos = graphfakos
        self._provider = provider
        self._request = request
        self._revision = _graph_revision(graphfakos, provider.load_graph(request))

    def open_live_session(self, request: Any) -> Any:
        return self._graphfakos.GraphFakosLiveSessionStatus(
            status="live",
            revision=self._revision,
            cursor=self._graphfakos.GraphFakosLiveSessionCursor(self._revision.value),
            message="OpenMinion memory graph live refresh is enabled.",
        )

    def load_patch(self, request: Any) -> Any:
        graph = self._provider.load_graph(self._request)
        current_revision = _graph_revision(self._graphfakos, graph)
        cursor = getattr(request, "cursor", None)
        base_value = (
            str(getattr(cursor, "value", "") or "").strip() or self._revision.value
        )
        if current_revision.value == base_value:
            return self._graphfakos.GraphFakosLiveSessionStatus(
                status="heartbeat",
                revision=current_revision,
                cursor=self._graphfakos.GraphFakosLiveSessionCursor(
                    current_revision.value
                ),
                message="No memory graph changes are available.",
            )
        patch = self._graphfakos.GraphFakosGraphPatch(
            patch_id=f"openminion-memory:{current_revision.value}",
            base_revision=self._graphfakos.GraphFakosGraphRevision(base_value),
            result_revision=current_revision,
            cursor=self._graphfakos.GraphFakosLiveSessionCursor(current_revision.value),
            operations=(
                self._graphfakos.GraphFakosPatchOperation(
                    kind="snapshot_reset",
                    graph=graph,
                    metadata={
                        "provider": OPENMINION_MEMORY_PROVIDER_ID,
                        "refresh_strategy": "live_snapshot_reset_or_requery",
                    },
                ),
            ),
        )
        self._revision = current_revision
        return patch

    def diagnostics(self) -> Any:
        return self._graphfakos.GraphFakosLiveSessionDiagnostics(
            last_revision=self._revision.value,
        )


def _graph_revision(graphfakos: Any, graph: Any) -> Any:
    payload = {
        "nodes": [
            {
                "id": str(getattr(node, "id", "") or ""),
                "label": str(getattr(node, "label", "") or ""),
                "kind": str(getattr(node, "kind", "") or ""),
                "summary": str(getattr(node, "summary", "") or ""),
                "score": getattr(node, "score", None),
                "source": str(getattr(node, "source", "") or ""),
                "tags": list(getattr(node, "tags", ()) or ()),
                "timestamps": dict(getattr(node, "timestamps", {}) or {}),
            }
            for node in getattr(graph, "nodes", ()) or ()
        ],
        "edges": [
            {
                "id": str(getattr(edge, "id", "") or ""),
                "source_id": str(getattr(edge, "source_id", "") or ""),
                "target_id": str(getattr(edge, "target_id", "") or ""),
                "kind": str(getattr(edge, "kind", "") or ""),
                "label": str(getattr(edge, "label", "") or ""),
            }
            for edge in getattr(graph, "edges", ()) or ()
        ],
        "stats": dict(getattr(graph, "stats", {}) or {}),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return graphfakos.GraphFakosGraphRevision(
        hashlib.sha256(encoded).hexdigest()[:16]
    )


__all__ = [
    "OPENMINION_MEMORY_PROVIDER_ID",
    "OpenMinionMemoryGraphFakosProvider",
    "OpenMinionMemoryGraphFakosLiveProvider",
    "memory_db_sample_count",
]
