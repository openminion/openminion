"""Second-brain memory provider adapter for the GraphFakos viewer."""

from __future__ import annotations

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
        return len(tuple(store.list(ListQueryOptions(limit=20))))
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
        relations: list[Any] = []
        for record in records:
            record_relations: tuple[Any, ...] = tuple(
                store.list_relations(record.id) or ()
            )
            for relation in record_relations:
                if (
                    relation.source_record_id in record_ids
                    and relation.target_record_id in record_ids
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
            else (f"No memory records found in {self._db_path}.",),
            stats={
                "db_path": str(self._db_path),
                "records": len(records),
                "relations": len(unique_relations),
                "scope_filter": list(scopes),
                "memory_types": _sorted_unique(str(record.type) for record in records),
                "tiers": _sorted_unique(
                    str(getattr(record, "tier", "") or "") for record in records
                ),
            },
            provider_details={
                "layer": LAYER_SECOND_BRAIN,
                "storage": "openminion memory SQLite",
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
            },
            available_facets={
                "node_kind": _sorted_unique(str(record.type) for record in records),
                "edge_kind": _sorted_unique(
                    str(relation.relation_type)
                    for relation in unique_relations.values()
                ),
                "source": _sorted_unique(
                    str(getattr(record, "source", "") or "") for record in records
                ),
                "tag": _sorted_unique(
                    tag
                    for record in records
                    for tag in _memory_record_tags(record, str(record.type))
                ),
            },
        )


def _scope_filters(request: Any) -> list[str]:
    raw_scope = str(getattr(request, "filters", {}).get("scope", "") or "").strip()
    if raw_scope:
        return [scope for scope in _split_scope_filter(raw_scope) if scope]
    return []


def _split_scope_filter(raw_scope: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(item.strip() for item in raw_scope.split(",") if item.strip())
    )


def _sorted_unique(values: Any) -> tuple[str, ...]:
    return tuple(sorted({str(value) for value in values if str(value)}))


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


__all__ = [
    "OPENMINION_MEMORY_PROVIDER_ID",
    "OpenMinionMemoryGraphFakosProvider",
    "memory_db_sample_count",
]
