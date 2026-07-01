"""Provider-neutral DTOs for the knowledge-graph layer."""

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from .constants import (
    KNOWLEDGE_GRAPH_CAPABILITIES,
    KNOWLEDGE_GRAPH_LAYERS,
    KNOWLEDGE_GRAPH_PROVIDER_TAGS,
)
from .errors import (
    InvalidCapabilityError,
    InvalidLayerError,
    InvalidProviderTagError,
)


def _normalize_str(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise InvalidLayerError(
            f"{field_name} is required",
            details={"field": field_name},
        )
    return text


def _validate_layer(value: Any) -> str:
    text = str(value or "").strip()
    if text not in KNOWLEDGE_GRAPH_LAYERS:
        raise InvalidLayerError(
            f"Unknown knowledge-graph layer {value!r}",
            details={"value": text, "valid": sorted(KNOWLEDGE_GRAPH_LAYERS)},
        )
    return text


def _validate_tags(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise InvalidProviderTagError(
            "tags must be a sequence, not a string",
            details={"value": values},
        )
    result: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if text not in KNOWLEDGE_GRAPH_PROVIDER_TAGS:
            raise InvalidProviderTagError(
                f"Unknown provider tag {raw!r}",
                details={
                    "value": text,
                    "valid": sorted(KNOWLEDGE_GRAPH_PROVIDER_TAGS),
                },
            )
        if text not in result:
            result.append(text)
    return tuple(result)


def _validate_capabilities(values: Any) -> frozenset[str]:
    if values is None:
        return frozenset()
    if isinstance(values, (str, bytes)):
        raise InvalidCapabilityError(
            "capabilities must be a sequence, not a string",
            details={"value": values},
        )
    result: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if text not in KNOWLEDGE_GRAPH_CAPABILITIES:
            raise InvalidCapabilityError(
                f"Unknown capability {raw!r}",
                details={
                    "value": text,
                    "valid": sorted(KNOWLEDGE_GRAPH_CAPABILITIES),
                },
            )
        result.add(text)
    return frozenset(result)


def _mapping_proxy(value: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


def _set_mapping_proxy(
    instance: object,
    field_name: str,
    value: Mapping[str, Any] | None = None,
) -> None:
    object.__setattr__(instance, field_name, _mapping_proxy(value))


def _frozen_edge_mappings(
    edges: tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(_mapping_proxy(edge) for edge in edges)


@dataclass(frozen=True)
class KnowledgeGraphCapabilities:
    """Set of advertised capabilities for a provider."""

    advertised: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "advertised", _validate_capabilities(self.advertised))

    def __contains__(self, name: object) -> bool:
        return str(name) in self.advertised

    def supports(self, name: str) -> bool:
        return name in self.advertised

    def as_tuple(self) -> tuple[str, ...]:
        return tuple(sorted(self.advertised))


@dataclass(frozen=True)
class KnowledgeGraphHealth:
    """Health snapshot returned by ``KnowledgeGraphSource.health``."""

    provider: str
    layer: str
    ok: bool
    detail: str = ""
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider", _normalize_str(self.provider, field_name="provider")
        )
        object.__setattr__(self, "layer", _validate_layer(self.layer))
        _set_mapping_proxy(self, "diagnostics", self.diagnostics)


@dataclass(frozen=True)
class GraphSourceRef:
    """Reference to the source file/document for a graph item."""

    path: str = ""
    page: int | None = None
    line: int | None = None
    span: tuple[int, int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "page": self.page,
            "line": self.line,
            "span": list(self.span) if self.span is not None else None,
        }


@dataclass(frozen=True)
class GraphContextItem:
    """One provider-neutral cited graph item."""

    provider: str
    source_graph_id: str
    node_or_edge_id: str
    source_ref: GraphSourceRef = field(default_factory=GraphSourceRef)
    snippet: str = ""
    score: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider", _normalize_str(self.provider, field_name="provider")
        )
        object.__setattr__(self, "source_graph_id", str(self.source_graph_id or ""))
        object.__setattr__(self, "node_or_edge_id", str(self.node_or_edge_id or ""))
        _set_mapping_proxy(self, "metadata", self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "source_graph_id": self.source_graph_id,
            "node_or_edge_id": self.node_or_edge_id,
            "source_ref": self.source_ref.as_dict(),
            "snippet": self.snippet,
            "score": self.score,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class GraphPathEvidence:
    """Path between two graph entities."""

    provider: str
    nodes: tuple[GraphContextItem, ...] = ()
    edges: tuple[Mapping[str, Any], ...] = ()
    explanation: str = ""
    score: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider", _normalize_str(self.provider, field_name="provider")
        )
        object.__setattr__(self, "edges", _frozen_edge_mappings(self.edges))

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [dict(edge) for edge in self.edges],
            "explanation": self.explanation,
            "score": self.score,
        }


@dataclass(frozen=True)
class GraphOmittedItem:
    """Reason a candidate item was not returned (budget, dedup, policy)."""

    provider: str
    node_or_edge_id: str = ""
    reason: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider", _normalize_str(self.provider, field_name="provider")
        )
        _set_mapping_proxy(self, "details", self.details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "node_or_edge_id": self.node_or_edge_id,
            "reason": self.reason,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class GraphQueryRequest:
    """Free-text or structured query request."""

    query: str = ""
    entity_ids: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    max_results: int | None = None
    max_chars: int | None = None
    include_paths: bool = False
    include_explanations: bool = False
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _set_mapping_proxy(self, "options", self.options)


@dataclass(frozen=True)
class GraphNeighborhoodRequest:
    """Neighborhood request around an entity or source reference."""

    entity_id: str = ""
    source_ref: GraphSourceRef | None = None
    depth: int = 1
    max_results: int | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _set_mapping_proxy(self, "options", self.options)


@dataclass(frozen=True)
class GraphPathRequest:
    """Bounded-path request between two entities."""

    source_entity_id: str
    target_entity_id: str
    max_hops: int = 4
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _set_mapping_proxy(self, "options", self.options)


@dataclass(frozen=True)
class GraphExplainRequest:
    """Explain why a node/edge/path matters."""

    target_id: str
    kind: str = "node"
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _set_mapping_proxy(self, "options", self.options)


@dataclass(frozen=True)
class GraphRefreshRequest:
    """Refresh / reindex request."""

    mode: str = "manual"
    full: bool = False
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _set_mapping_proxy(self, "options", self.options)


@dataclass(frozen=True)
class GraphQueryResult:
    """Provider-neutral result envelope.

    Shared by second-brain Sophiagraph retrieval and third-brain Graphify
    retrieval so context assembly can budget/dedup/cite uniformly.
    """

    provider: str
    layer: str
    tags: tuple[str, ...] = ()
    items: tuple[GraphContextItem, ...] = ()
    paths: tuple[GraphPathEvidence, ...] = ()
    omitted: tuple[GraphOmittedItem, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider", _normalize_str(self.provider, field_name="provider")
        )
        object.__setattr__(self, "layer", _validate_layer(self.layer))
        object.__setattr__(self, "tags", _validate_tags(self.tags))
        _set_mapping_proxy(self, "diagnostics", self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "layer": self.layer,
            "tags": list(self.tags),
            "items": [item.to_dict() for item in self.items],
            "paths": [path.to_dict() for path in self.paths],
            "omitted": [omit.to_dict() for omit in self.omitted],
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class GraphPathResult:
    """Result envelope for path queries."""

    provider: str
    layer: str
    paths: tuple[GraphPathEvidence, ...] = ()
    omitted: tuple[GraphOmittedItem, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider", _normalize_str(self.provider, field_name="provider")
        )
        object.__setattr__(self, "layer", _validate_layer(self.layer))
        _set_mapping_proxy(self, "diagnostics", self.diagnostics)


@dataclass(frozen=True)
class GraphExplainResult:
    """Result envelope for explain queries."""

    provider: str
    layer: str
    target_id: str
    explanation: str = ""
    evidence: tuple[GraphContextItem, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider", _normalize_str(self.provider, field_name="provider")
        )
        object.__setattr__(self, "layer", _validate_layer(self.layer))
        _set_mapping_proxy(self, "diagnostics", self.diagnostics)


@dataclass(frozen=True)
class GraphRefreshResult:
    """Result envelope for refresh operations."""

    provider: str
    layer: str
    ok: bool
    refreshed_at: str = ""
    counts: Mapping[str, int] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider", _normalize_str(self.provider, field_name="provider")
        )
        object.__setattr__(self, "layer", _validate_layer(self.layer))
        object.__setattr__(
            self,
            "counts",
            MappingProxyType(
                {str(k): int(v) for k, v in dict(self.counts or {}).items()}
            ),
        )
        _set_mapping_proxy(self, "diagnostics", self.diagnostics)
