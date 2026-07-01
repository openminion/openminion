"""Graphify adapter for OpenMinion third-brain graph context."""

import json
import subprocess
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..config import DEFAULT_GRAPHIFY_TIMEOUT_SECONDS, KnowledgeGraphProviderConfig
from ..constants import (
    CAPABILITY_CITATIONS,
    CAPABILITY_EXPLAIN,
    CAPABILITY_PATH,
    CAPABILITY_PROVENANCE,
    CAPABILITY_QUERY,
    CAPABILITY_REFRESH,
    GRAPHIFY_OPTION_CAPABILITIES,
    GRAPHIFY_OPTION_COMMAND,
    GRAPHIFY_OPTION_COMMAND_ARGS,
    GRAPHIFY_OPTION_GRAPH_ID,
    GRAPHIFY_OPTION_GRAPH_PATH,
    GRAPHIFY_OPTION_TIMEOUT_SECONDS,
    LAYER_THIRD_BRAIN,
    PROVIDER_GRAPHIFY,
    TAG_CODE_GRAPH,
    TAG_DOCUMENT_GRAPH,
)
from ..errors import UnsupportedCapabilityError
from ..interfaces import KNOWLEDGE_GRAPH_INTERFACE_VERSION
from ..models import (
    GraphContextItem,
    GraphExplainRequest,
    GraphExplainResult,
    GraphNeighborhoodRequest,
    GraphOmittedItem,
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

GraphifyCommandRunner = Callable[[Sequence[str], float], "GraphifyCommandResult"]


@dataclass(frozen=True)
class GraphifyCommandResult:
    """Result returned by an injected Graphify command runner."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class GraphifyKnowledgeGraphSource:
    """Read-oriented Graphify provider backed by graph artifacts or refresh command."""

    contract_version = KNOWLEDGE_GRAPH_INTERFACE_VERSION

    def __init__(
        self,
        *,
        config: KnowledgeGraphProviderConfig,
        layer: str = LAYER_THIRD_BRAIN,
        runner: GraphifyCommandRunner | None = None,
    ) -> None:
        self._config = config
        self._layer = layer
        self._runner = runner or _run_graphify_command
        self._options = dict(config.options or {})
        self._payload: Mapping[str, Any] = {}
        self._load_error = ""
        self._load_graph_payload()

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def layer(self) -> str:
        return self._layer

    @property
    def tags(self) -> tuple[str, ...]:
        return self._config.tags or (TAG_CODE_GRAPH, TAG_DOCUMENT_GRAPH)

    @property
    def capabilities(self) -> KnowledgeGraphCapabilities:
        return KnowledgeGraphCapabilities(
            advertised=_advertised_capabilities(self._config)
        )

    def health(self) -> KnowledgeGraphHealth:
        graph_path = _option_text(self._options, GRAPHIFY_OPTION_GRAPH_PATH)
        has_command = bool(_command_args(self._options))
        ok = bool(self._payload) or has_command
        detail = "ready" if ok else "graph artifact missing and no command configured"
        if self._load_error:
            detail = self._load_error
        return KnowledgeGraphHealth(
            provider=self.name,
            layer=self.layer,
            ok=ok,
            detail=detail,
            diagnostics={
                "graph_path": graph_path,
                "has_command": has_command,
                "node_count": len(_graph_nodes(self._payload)),
                "edge_count": len(_graph_edges(self._payload)),
            },
        )

    def query(self, request: GraphQueryRequest) -> GraphQueryResult:
        self._ensure_capability(CAPABILITY_QUERY)
        ranked = _rank_nodes(_graph_nodes(self._payload), request)
        max_results = request.max_results or self._config.retrieval.max_results
        max_chars = request.max_chars or self._config.retrieval.max_chars
        items: list[GraphContextItem] = []
        omitted: list[GraphOmittedItem] = []
        used_chars = 0
        for score, node in ranked:
            item = _node_to_item(self.name, _graph_id(self._options), node, score=score)
            item_chars = len(item.snippet)
            if len(items) >= max_results or used_chars + item_chars > max_chars:
                omitted.append(
                    GraphOmittedItem(
                        provider=self.name,
                        node_or_edge_id=item.node_or_edge_id,
                        reason="budget",
                    )
                )
                continue
            used_chars += item_chars
            items.append(item)
        return GraphQueryResult(
            provider=self.name,
            layer=self.layer,
            tags=self.tags,
            items=tuple(items),
            omitted=tuple(omitted),
            diagnostics={
                "graph_id": _graph_id(self._options),
                "candidate_count": len(ranked),
            },
        )

    def neighborhood(self, request: GraphNeighborhoodRequest) -> GraphQueryResult:
        self._ensure_capability(CAPABILITY_QUERY)
        if not request.entity_id:
            return GraphQueryResult(
                provider=self.name, layer=self.layer, tags=self.tags
            )
        path_request = GraphPathRequest(
            source_entity_id=request.entity_id,
            target_entity_id=request.entity_id,
            max_hops=max(request.depth, 1),
            options={"neighborhood": True},
        )
        path_result = self.path(path_request)
        items = tuple(
            node
            for path in path_result.paths
            for node in path.nodes[: request.max_results or None]
        )
        return GraphQueryResult(
            provider=self.name,
            layer=self.layer,
            tags=self.tags,
            items=items,
            paths=path_result.paths,
            omitted=path_result.omitted,
            diagnostics=dict(path_result.diagnostics),
        )

    def path(self, request: GraphPathRequest) -> GraphPathResult:
        self._ensure_capability(CAPABILITY_PATH)
        nodes_by_id = _nodes_by_id(_graph_nodes(self._payload))
        edges = _graph_edges(self._payload)
        node_ids, path_edges = _find_path(
            request.source_entity_id,
            request.target_entity_id,
            nodes_by_id=nodes_by_id,
            edges=edges,
            max_hops=request.max_hops,
            neighborhood=bool(request.options.get("neighborhood")),
        )
        if not node_ids:
            return GraphPathResult(
                provider=self.name,
                layer=self.layer,
                omitted=(
                    GraphOmittedItem(
                        provider=self.name,
                        reason="not_found",
                        details={
                            "source": request.source_entity_id,
                            "target": request.target_entity_id,
                        },
                    ),
                ),
            )
        graph_id = _graph_id(self._options)
        evidence = GraphPathEvidence(
            provider=self.name,
            nodes=tuple(
                _node_to_item(self.name, graph_id, nodes_by_id[node_id])
                for node_id in node_ids
            ),
            edges=tuple(path_edges),
        )
        return GraphPathResult(
            provider=self.name,
            layer=self.layer,
            paths=(evidence,),
            diagnostics={"graph_id": graph_id},
        )

    def explain(self, request: GraphExplainRequest) -> GraphExplainResult:
        self._ensure_capability(CAPABILITY_EXPLAIN)
        node = _nodes_by_id(_graph_nodes(self._payload)).get(request.target_id)
        if node is None:
            return GraphExplainResult(
                provider=self.name,
                layer=self.layer,
                target_id=request.target_id,
                diagnostics={"found": False},
            )
        item = _node_to_item(self.name, _graph_id(self._options), node)
        explanation = _first_text(
            node,
            (
                "explanation",
                "summary",
                "description",
                "snippet",
                "text",
                "label",
                "name",
            ),
        )
        return GraphExplainResult(
            provider=self.name,
            layer=self.layer,
            target_id=request.target_id,
            explanation=explanation,
            evidence=(item,),
            diagnostics={"found": True},
        )

    def refresh(self, request: GraphRefreshRequest) -> GraphRefreshResult:
        self._ensure_capability(CAPABILITY_REFRESH)
        args = _command_args(self._options)
        if not args:
            return GraphRefreshResult(
                provider=self.name,
                layer=self.layer,
                ok=False,
                diagnostics={"error": "no command configured"},
            )
        timeout = _timeout_seconds(self._options)
        result = self._runner(args, timeout)
        if result.returncode == 0:
            self._load_graph_payload()
        return GraphRefreshResult(
            provider=self.name,
            layer=self.layer,
            ok=result.returncode == 0,
            counts={
                "nodes": len(_graph_nodes(self._payload)),
                "edges": len(_graph_edges(self._payload)),
            },
            diagnostics={
                "returncode": result.returncode,
                "stderr": result.stderr,
                "mode": request.mode,
            },
        )

    def _ensure_capability(self, capability: str) -> None:
        if not self.capabilities.supports(capability):
            raise UnsupportedCapabilityError(
                f"Graphify provider {self.name!r} does not advertise {capability}",
                details={"provider": self.name, "capability": capability},
            )

    def _load_graph_payload(self) -> None:
        graph_path = _option_text(self._options, GRAPHIFY_OPTION_GRAPH_PATH)
        if not graph_path:
            self._payload = {}
            self._load_error = ""
            return
        path = Path(graph_path).expanduser()
        try:
            self._payload = json.loads(path.read_text(encoding="utf-8"))
            self._load_error = ""
        except FileNotFoundError:
            self._payload = {}
            self._load_error = f"graph artifact not found: {graph_path}"
        except json.JSONDecodeError as exc:
            self._payload = {}
            self._load_error = f"graph artifact is not valid JSON: {exc}"


def _run_graphify_command(args: Sequence[str], timeout: float) -> GraphifyCommandResult:
    completed = subprocess.run(
        list(args),
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    return GraphifyCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _advertised_capabilities(config: KnowledgeGraphProviderConfig) -> frozenset[str]:
    configured = config.options.get(GRAPHIFY_OPTION_CAPABILITIES)
    if isinstance(configured, (list, tuple, set)):
        return frozenset(
            str(value).strip() for value in configured if str(value).strip()
        )
    capabilities = {
        CAPABILITY_QUERY,
        CAPABILITY_CITATIONS,
        CAPABILITY_PROVENANCE,
    }
    if CAPABILITY_PATH in config.optional_capabilities:
        capabilities.add(CAPABILITY_PATH)
    if CAPABILITY_EXPLAIN in config.optional_capabilities:
        capabilities.add(CAPABILITY_EXPLAIN)
    if _command_args(config.options):
        capabilities.add(CAPABILITY_REFRESH)
    return frozenset(capabilities)


def _option_text(options: Mapping[str, Any], key: str) -> str:
    return str(options.get(key) or "").strip()


def _timeout_seconds(options: Mapping[str, Any]) -> float:
    raw = options.get(GRAPHIFY_OPTION_TIMEOUT_SECONDS, DEFAULT_GRAPHIFY_TIMEOUT_SECONDS)
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_GRAPHIFY_TIMEOUT_SECONDS
    return max(parsed, 0.1)


def _command_args(options: Mapping[str, Any]) -> tuple[str, ...]:
    command = options.get(GRAPHIFY_OPTION_COMMAND)
    if isinstance(command, (list, tuple)):
        base = tuple(str(part) for part in command if str(part).strip())
    else:
        text = str(command or "").strip()
        base = (text,) if text else ()
    extra = options.get(GRAPHIFY_OPTION_COMMAND_ARGS)
    if isinstance(extra, (list, tuple)):
        return (*base, *(str(part) for part in extra if str(part).strip()))
    return base


def _graph_id(options: Mapping[str, Any]) -> str:
    return _option_text(options, GRAPHIFY_OPTION_GRAPH_ID) or PROVIDER_GRAPHIFY


def _graph_nodes(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = payload.get("nodes") or payload.get("items") or payload.get("documents") or ()
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _graph_edges(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = payload.get("edges") or payload.get("links") or payload.get("relations") or ()
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def _nodes_by_id(nodes: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for node in nodes:
        node_id = _node_id(node)
        if node_id:
            result[node_id] = node
    return result


def _rank_nodes(
    nodes: Sequence[Mapping[str, Any]],
    request: GraphQueryRequest,
) -> list[tuple[float, Mapping[str, Any]]]:
    entity_ids = {str(entity_id) for entity_id in request.entity_ids if str(entity_id)}
    query_text = request.query.strip().lower()
    terms = tuple(term for term in query_text.split() if term)
    ranked: list[tuple[float, Mapping[str, Any]]] = []
    for node in nodes:
        node_id = _node_id(node)
        text = _search_text(node)
        if entity_ids and node_id not in entity_ids:
            continue
        if terms:
            hits = sum(1 for term in terms if term in text)
            if hits == 0:
                continue
            score = float(hits)
            label = _first_text(node, ("label", "name", "title"))
            if query_text and node_id.lower() == query_text:
                score += 200.0
            if query_text and label.lower() == query_text:
                score += 100.0
            ranked.append((score, node))
            continue
        if entity_ids:
            ranked.append((1.0, node))
    ranked.sort(key=lambda item: (-item[0], _node_id(item[1])))
    return ranked


def _search_text(value: Any) -> str:
    parts: list[str] = []
    _collect_text(value, parts)
    return " ".join(parts).lower()


def _collect_text(value: Any, parts: list[str]) -> None:
    if isinstance(value, str):
        parts.append(value)
        return
    if isinstance(value, Mapping):
        for nested in value.values():
            _collect_text(nested, parts)
        return
    if isinstance(value, list | tuple):
        for nested in value:
            _collect_text(nested, parts)


def _node_to_item(
    provider: str,
    graph_id: str,
    node: Mapping[str, Any],
    *,
    score: float | None = None,
) -> GraphContextItem:
    return GraphContextItem(
        provider=provider,
        source_graph_id=graph_id,
        node_or_edge_id=_node_id(node),
        source_ref=_source_ref(node),
        snippet=_first_text(
            node,
            (
                "snippet",
                "text",
                "content",
                "summary",
                "description",
                "label",
                "name",
                "title",
            ),
        ),
        score=score,
        metadata=_node_metadata(node),
    )


def _node_metadata(node: Mapping[str, Any]) -> dict[str, Any]:
    properties = _node_properties(node)
    metadata = node.get("metadata")
    if isinstance(metadata, Mapping):
        result = dict(metadata)
    else:
        nested = properties.get("metadata")
        result = dict(nested) if isinstance(nested, Mapping) else {}
    kind = node.get("kind") or node.get("type") or properties.get("kind")
    if str(kind or "").strip() and "kind" not in result:
        result["kind"] = str(kind).strip()
    return result


def _node_properties(node: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = node.get("properties")
    return raw if isinstance(raw, Mapping) else {}


def _source_ref(node: Mapping[str, Any]) -> GraphSourceRef:
    properties = _node_properties(node)
    raw_source = node.get("source")
    source: Mapping[str, Any] = raw_source if isinstance(raw_source, Mapping) else {}
    nested_source = properties.get("source_ref")
    source_ref: Mapping[str, Any] = (
        nested_source if isinstance(nested_source, Mapping) else {}
    )
    source_path = (
        node.get("path")
        or node.get("file")
        or node.get("source_path")
        or source.get("path")
        or source.get("file")
        or source_ref.get("path")
        or ""
    )
    return GraphSourceRef(
        path=str(source_path),
        page=_optional_int(
            node.get("page") or source.get("page") or source_ref.get("page")
        ),
        line=_optional_int(
            node.get("line") or source.get("line") or source_ref.get("line")
        ),
        span=_optional_span(
            node.get("span") or source.get("span") or source_ref.get("span")
        ),
    )


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_span(value: Any) -> tuple[int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    start = _optional_int(value[0])
    end = _optional_int(value[1])
    if start is None or end is None:
        return None
    return (start, end)


def _first_text(node: Mapping[str, Any], keys: Sequence[str]) -> str:
    properties = _node_properties(node)
    for key in keys:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        nested = properties.get(key)
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return _node_id(node)


def _node_id(node: Mapping[str, Any]) -> str:
    for key in ("id", "node_id", "uid", "path", "name", "label", "title"):
        value = node.get(key)
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _edge_source(edge: Mapping[str, Any]) -> str:
    return str(
        edge.get("source") or edge.get("from") or edge.get("start") or ""
    ).strip()


def _edge_target(edge: Mapping[str, Any]) -> str:
    return str(edge.get("target") or edge.get("to") or edge.get("end") or "").strip()


def _find_path(
    source: str,
    target: str,
    *,
    nodes_by_id: Mapping[str, Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    max_hops: int,
    neighborhood: bool = False,
) -> tuple[tuple[str, ...], tuple[Mapping[str, Any], ...]]:
    if source not in nodes_by_id:
        return (), ()
    if neighborhood:
        adjacent = [
            edge
            for edge in edges
            if _edge_source(edge) == source or _edge_target(edge) == source
        ][:max_hops]
        node_ids = [source]
        for edge in adjacent:
            for node_id in (_edge_source(edge), _edge_target(edge)):
                if node_id in nodes_by_id and node_id not in node_ids:
                    node_ids.append(node_id)
        return tuple(node_ids), tuple(adjacent)
    queue: deque[tuple[str, tuple[str, ...], tuple[Mapping[str, Any], ...]]] = deque(
        [(source, (source,), ())]
    )
    visited = {source}
    while queue:
        current, path, path_edges = queue.popleft()
        if current == target:
            return path, path_edges
        if len(path) - 1 >= max_hops:
            continue
        for edge in edges:
            if _edge_source(edge) != current:
                continue
            next_id = _edge_target(edge)
            if next_id not in nodes_by_id or next_id in visited:
                continue
            visited.add(next_id)
            queue.append((next_id, (*path, next_id), (*path_edges, edge)))
    return (), ()
