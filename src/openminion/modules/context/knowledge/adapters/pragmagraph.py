"""PragmaGraph adapter for OpenMinion third-brain graph context."""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable, Mapping, Sequence

from ..config import DEFAULT_GRAPHIFY_TIMEOUT_SECONDS, KnowledgeGraphProviderConfig
from ..constants import (
    CAPABILITY_CITATIONS,
    CAPABILITY_NEIGHBORHOOD,
    CAPABILITY_PATH,
    CAPABILITY_PROVENANCE,
    CAPABILITY_QUERY,
    CAPABILITY_REFRESH,
    LAYER_THIRD_BRAIN,
    PRAGMAGRAPH_OPTION_CAPABILITIES,
    PRAGMAGRAPH_OPTION_COMMAND,
    PRAGMAGRAPH_OPTION_COMMAND_ARGS,
    PRAGMAGRAPH_OPTION_GRAPH_ID,
    PRAGMAGRAPH_OPTION_NAMESPACE,
    PRAGMAGRAPH_OPTION_ROOT_PATH,
    PRAGMAGRAPH_OPTION_SNAPSHOT_PATH,
    PRAGMAGRAPH_OPTION_TIMEOUT_SECONDS,
    PROVIDER_PRAGMAGRAPH,
    TAG_CODE_GRAPH,
    TAG_DOCUMENT_GRAPH,
)
from ..errors import KnowledgeGraphError, UnsupportedCapabilityError
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

PragmaGraphCommandRunner = Callable[[Sequence[str], float], "PragmaGraphCommandResult"]


@dataclass(frozen=True)
class PragmaGraphCommandResult:
    """Result returned by an injected PragmaGraph refresh command runner."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class PragmaGraphKnowledgeGraphSource:
    """Read-oriented PragmaGraph provider backed by package snapshots."""

    contract_version = KNOWLEDGE_GRAPH_INTERFACE_VERSION

    def __init__(
        self,
        *,
        config: KnowledgeGraphProviderConfig,
        layer: str = LAYER_THIRD_BRAIN,
        runner: PragmaGraphCommandRunner | None = None,
    ) -> None:
        self._config = config
        self._layer = layer
        self._runner = runner or _run_pragmagraph_command
        self._options = dict(config.options or {})
        self._snapshot: Any | None = None
        self._load_error = ""
        self._package_error = ""
        self._package: Any | None = None
        self._load_snapshot()

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
            advertised=_pragmagraph_advertised_capabilities(self._config)
        )

    def health(self) -> KnowledgeGraphHealth:
        snapshot = self._snapshot
        ok = snapshot is not None and not self._package_error and not self._load_error
        detail = "ready" if ok else self._package_error or self._load_error
        if not detail:
            detail = "snapshot not loaded"
        diagnostics = {
            "graph_id": self._graph_id(),
            "namespace": _snapshot_text(snapshot, "namespace"),
            "schema_version": _snapshot_text(snapshot, "schema_version"),
            "snapshot_path": self._snapshot_path_text(),
            "package_version": str(getattr(self._package, "__version__", "") or ""),
            "node_count": len(tuple(getattr(snapshot, "nodes", ()) or ())),
            "edge_count": len(tuple(getattr(snapshot, "edges", ()) or ())),
            "omitted_count": len(tuple(getattr(snapshot, "omitted", ()) or ())),
        }
        return KnowledgeGraphHealth(
            provider=self.name,
            layer=self.layer,
            ok=ok,
            detail=detail,
            diagnostics=diagnostics,
        )

    def query(self, request: GraphQueryRequest) -> GraphQueryResult:
        self._ensure_capability(CAPABILITY_QUERY)
        query_mod = self._module("pragmagraph.query")
        models_mod = self._module("pragmagraph.models")
        snapshot = self._require_snapshot()
        query_request = models_mod.QueryRequest(
            query=request.query,
            max_results=request.max_results or self._config.retrieval.max_results,
            include_edges=True,
        )
        result = query_mod.query(snapshot, query_request)
        return _query_result_to_openminion(
            provider=self.name,
            layer=self.layer,
            tags=self.tags,
            graph_id=self._graph_id(snapshot),
            snapshot=snapshot,
            result=result,
        )

    def neighborhood(self, request: GraphNeighborhoodRequest) -> GraphQueryResult:
        self._ensure_capability(CAPABILITY_NEIGHBORHOOD)
        query_mod = self._module("pragmagraph.query")
        snapshot = self._require_snapshot()
        if not request.entity_id:
            return GraphQueryResult(
                provider=self.name,
                layer=self.layer,
                tags=self.tags,
                omitted=(
                    GraphOmittedItem(
                        provider=self.name,
                        reason="missing_entity_id",
                    ),
                ),
            )
        result = query_mod.neighborhood(
            snapshot,
            request.entity_id,
            depth=request.depth,
            max_results=request.max_results or self._config.retrieval.max_results,
        )
        return _query_result_to_openminion(
            provider=self.name,
            layer=self.layer,
            tags=self.tags,
            graph_id=self._graph_id(snapshot),
            snapshot=snapshot,
            result=result,
        )

    def path(self, request: GraphPathRequest) -> GraphPathResult:
        self._ensure_capability(CAPABILITY_PATH)
        query_mod = self._module("pragmagraph.query")
        snapshot = self._require_snapshot()
        result = query_mod.path(
            snapshot,
            request.source_entity_id,
            request.target_entity_id,
            max_hops=request.max_hops,
        )
        omitted = tuple(
            _omitted_to_openminion(self.name, item)
            for item in getattr(result, "omitted", ()) or ()
        )
        evidence: tuple[GraphPathEvidence, ...] = ()
        nodes = tuple(getattr(result, "nodes", ()) or ())
        edges = tuple(getattr(result, "edges", ()) or ())
        if nodes or edges:
            graph_id = self._graph_id(snapshot)
            evidence = (
                GraphPathEvidence(
                    provider=self.name,
                    nodes=tuple(
                        _pragmagraph_node_to_item(self.name, graph_id, node)
                        for node in nodes
                    ),
                    edges=tuple(_edge_to_mapping(edge) for edge in edges),
                ),
            )
        return GraphPathResult(
            provider=self.name,
            layer=self.layer,
            paths=evidence,
            omitted=omitted,
            diagnostics={
                "graph_id": self._graph_id(snapshot),
                "schema_version": _snapshot_text(snapshot, "schema_version"),
            },
        )

    def explain(self, request: GraphExplainRequest) -> GraphExplainResult:
        del request
        self._ensure_capability("explain")
        return GraphExplainResult(provider=self.name, layer=self.layer, target_id="")

    def refresh(self, request: GraphRefreshRequest) -> GraphRefreshResult:
        self._ensure_capability(CAPABILITY_REFRESH)
        args = _pragmagraph_command_args(self._options)
        diagnostics: dict[str, Any] = {"mode": request.mode}
        refresh_result: Any | None = None
        ok = False
        if args:
            timeout = _pragmagraph_timeout_seconds(self._options)
            result = self._runner(args, timeout)
            diagnostics.update(
                {
                    "returncode": result.returncode,
                    "stderr": result.stderr,
                    "refresh": "command",
                }
            )
            ok = result.returncode == 0
            if ok:
                self._load_snapshot()
        else:
            root_path = _pragmagraph_option_text(
                self._options, PRAGMAGRAPH_OPTION_ROOT_PATH
            )
            snapshot_path = self._snapshot_path_text()
            if not root_path or not snapshot_path:
                return GraphRefreshResult(
                    provider=self.name,
                    layer=self.layer,
                    ok=False,
                    diagnostics={
                        **diagnostics,
                        "error": "root_path and snapshot_path are required",
                    },
                )
            package = self._load_package()
            previous_snapshot = self._snapshot
            refresh_result = package.refresh_snapshot(
                root_path,
                namespace=self._namespace(),
                previous_snapshot=previous_snapshot,
            )
            snapshot = refresh_result.snapshot
            package.save_snapshot(snapshot, snapshot_path)
            self._snapshot = snapshot
            self._load_error = ""
            diagnostics["refresh"] = "api"
            diagnostics["omitted_reason_counts"] = _omitted_reason_counts(snapshot)
            ok = True
        snapshot = self._snapshot
        return GraphRefreshResult(
            provider=self.name,
            layer=self.layer,
            ok=ok,
            counts=_refresh_counts(snapshot, refresh_result),
            diagnostics=diagnostics,
        )

    def _ensure_capability(self, capability: str) -> None:
        if not self.capabilities.supports(capability):
            raise UnsupportedCapabilityError(
                f"PragmaGraph provider {self.name!r} does not advertise {capability}",
                details={"provider": self.name, "capability": capability},
            )

    def _module(self, name: str) -> Any:
        try:
            return import_module(name)
        except ImportError as exc:
            raise KnowledgeGraphError(
                "PragmaGraph package is not installed",
                details={"provider": self.name, "module": name},
            ) from exc

    def _load_package(self) -> Any:
        if self._package is None:
            self._package = self._module("pragmagraph")
            self._package_error = ""
        return self._package

    def _load_snapshot(self) -> None:
        snapshot_path = self._snapshot_path_text()
        if not snapshot_path:
            self._snapshot = None
            self._load_error = "snapshot_path is not configured"
            return
        try:
            package = self._load_package()
            self._snapshot = package.load_snapshot(snapshot_path)
            self._load_error = ""
        except ImportError as exc:
            self._snapshot = None
            self._package_error = str(exc)
            self._load_error = "PragmaGraph package is not installed"
        except Exception as exc:
            self._snapshot = None
            self._load_error = str(exc)

    def _require_snapshot(self) -> Any:
        if self._snapshot is None:
            self._load_snapshot()
        if self._snapshot is None:
            raise KnowledgeGraphError(
                "PragmaGraph snapshot is not available",
                details={
                    "provider": self.name,
                    "snapshot_path": self._snapshot_path_text(),
                    "error": self._package_error or self._load_error,
                },
            )
        return self._snapshot

    def _snapshot_path_text(self) -> str:
        return _pragmagraph_option_text(self._options, PRAGMAGRAPH_OPTION_SNAPSHOT_PATH)

    def _namespace(self) -> str:
        return (
            _pragmagraph_option_text(self._options, PRAGMAGRAPH_OPTION_NAMESPACE)
            or self.name
            or PROVIDER_PRAGMAGRAPH
        )

    def _graph_id(self, snapshot: Any | None = None) -> str:
        return (
            _pragmagraph_option_text(self._options, PRAGMAGRAPH_OPTION_GRAPH_ID)
            or _snapshot_text(snapshot or self._snapshot, "namespace")
            or self._namespace()
        )


def _run_pragmagraph_command(
    args: Sequence[str], timeout: float
) -> PragmaGraphCommandResult:
    completed = subprocess.run(
        list(args),
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )
    return PragmaGraphCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _pragmagraph_advertised_capabilities(
    config: KnowledgeGraphProviderConfig,
) -> frozenset[str]:
    configured = config.options.get(PRAGMAGRAPH_OPTION_CAPABILITIES)
    if isinstance(configured, (list, tuple, set)):
        return frozenset(
            str(value).strip() for value in configured if str(value).strip()
        )
    capabilities = {
        CAPABILITY_QUERY,
        CAPABILITY_NEIGHBORHOOD,
        CAPABILITY_PATH,
        CAPABILITY_CITATIONS,
        CAPABILITY_PROVENANCE,
    }
    if _pragmagraph_command_args(config.options) or _pragmagraph_option_text(
        config.options, PRAGMAGRAPH_OPTION_ROOT_PATH
    ):
        capabilities.add(CAPABILITY_REFRESH)
    return frozenset(capabilities)


def _pragmagraph_option_text(options: Mapping[str, Any], key: str) -> str:
    return str(options.get(key) or "").strip()


def _pragmagraph_timeout_seconds(options: Mapping[str, Any]) -> float:
    raw = options.get(
        PRAGMAGRAPH_OPTION_TIMEOUT_SECONDS, DEFAULT_GRAPHIFY_TIMEOUT_SECONDS
    )
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_GRAPHIFY_TIMEOUT_SECONDS
    return max(parsed, 0.1)


def _pragmagraph_command_args(options: Mapping[str, Any]) -> tuple[str, ...]:
    command = options.get(PRAGMAGRAPH_OPTION_COMMAND)
    if isinstance(command, (list, tuple)):
        base = tuple(str(part) for part in command if str(part).strip())
    else:
        text = str(command or "").strip()
        base = (text,) if text else ()
    extra = options.get(PRAGMAGRAPH_OPTION_COMMAND_ARGS)
    if isinstance(extra, (list, tuple)):
        return (*base, *(str(part) for part in extra if str(part).strip()))
    return base


def _snapshot_text(snapshot: Any | None, attr: str) -> str:
    return str(getattr(snapshot, attr, "") or "") if snapshot is not None else ""


def _source_ref_to_openminion(source_ref: Any) -> GraphSourceRef:
    column = getattr(source_ref, "column", None)
    end_column = getattr(source_ref, "end_column", None)
    span = None
    if column is not None and end_column is not None:
        span = (int(column), int(end_column))
    return GraphSourceRef(
        path=str(getattr(source_ref, "path", "") or ""),
        line=getattr(source_ref, "line", None),
        span=span,
    )


def _pragmagraph_node_to_item(
    provider: str,
    graph_id: str,
    node: Any,
    *,
    score: float | None = None,
    snippet: str = "",
    edge_ids: tuple[str, ...] = (),
) -> GraphContextItem:
    node_metadata = dict(getattr(node, "metadata", {}) or {})
    return GraphContextItem(
        provider=provider,
        source_graph_id=graph_id,
        node_or_edge_id=str(getattr(node, "id", "") or ""),
        source_ref=_source_ref_to_openminion(getattr(node, "source_ref", None)),
        snippet=snippet
        or str(getattr(node, "text", "") or "")
        or str(getattr(node, "label", "") or ""),
        score=score,
        metadata={
            "kind": str(getattr(node, "kind", "") or ""),
            "label": str(getattr(node, "label", "") or ""),
            "node_metadata": node_metadata,
            "edge_ids": list(edge_ids),
        },
    )


def _edge_to_mapping(edge: Any) -> Mapping[str, Any]:
    to_dict = getattr(edge, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, Mapping):
            return dict(payload)
    return {
        "id": str(getattr(edge, "id", "") or ""),
        "kind": str(getattr(edge, "kind", "") or ""),
        "source_id": str(getattr(edge, "source_id", "") or ""),
        "target_id": str(getattr(edge, "target_id", "") or ""),
    }


def _omitted_to_openminion(provider: str, item: Any) -> GraphOmittedItem:
    return GraphOmittedItem(
        provider=provider,
        node_or_edge_id=str(getattr(item, "item_id", "") or ""),
        reason=str(getattr(item, "reason", "") or ""),
        details=dict(getattr(item, "details", {}) or {}),
    )


def _omitted_reason_counts(snapshot: Any | None) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                str(getattr(item, "reason", "") or "")
                for item in tuple(getattr(snapshot, "omitted", ()) or ())
                if str(getattr(item, "reason", "") or "")
            ).items()
        )
    )


def _refresh_counts(snapshot: Any | None, refresh_result: Any | None) -> dict[str, int]:
    snapshot_delta = getattr(refresh_result, "snapshot_delta", None)
    return {
        "nodes": _sequence_count(snapshot, "nodes"),
        "edges": _sequence_count(snapshot, "edges"),
        "changed_path_count": _sequence_count(refresh_result, "changed_paths"),
        "removed_path_count": _sequence_count(refresh_result, "removed_paths"),
        "added_node_count": _sequence_count(snapshot_delta, "added_node_ids"),
        "removed_node_count": _sequence_count(snapshot_delta, "removed_node_ids"),
        "added_edge_count": _sequence_count(snapshot_delta, "added_edge_ids"),
        "removed_edge_count": _sequence_count(snapshot_delta, "removed_edge_ids"),
        "added_omitted_count": _sequence_count(snapshot_delta, "added_omitted_ids"),
        "removed_omitted_count": _sequence_count(snapshot_delta, "removed_omitted_ids"),
    }


def _sequence_count(owner: Any | None, attribute: str) -> int:
    return len(tuple(getattr(owner, attribute, ()) or ()))


def _query_result_to_openminion(
    *,
    provider: str,
    layer: str,
    tags: tuple[str, ...],
    graph_id: str,
    snapshot: Any,
    result: Any,
) -> GraphQueryResult:
    items = tuple(
        _pragmagraph_node_to_item(
            provider,
            graph_id,
            getattr(hit, "node"),
            score=float(getattr(hit, "score", 0.0)),
            snippet=str(getattr(hit, "snippet", "") or ""),
            edge_ids=tuple(
                str(getattr(edge, "id", "") or "")
                for edge in tuple(getattr(hit, "edges", ()) or ())
            ),
        )
        for hit in tuple(getattr(result, "hits", ()) or ())
    )
    omitted = tuple(
        _omitted_to_openminion(provider, item)
        for item in tuple(getattr(result, "omitted", ()) or ())
    )
    diagnostics = {
        "graph_id": graph_id,
        "schema_version": _snapshot_text(snapshot, "schema_version"),
        "indexer_version": _snapshot_text(snapshot, "indexer_version"),
        **dict(getattr(result, "diagnostics", {}) or {}),
    }
    return GraphQueryResult(
        provider=provider,
        layer=layer,
        tags=tags,
        items=items,
        omitted=omitted,
        diagnostics=diagnostics,
    )
