"""Runtime service for configured OpenMinion graph-provider adapters."""

from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from .config import KnowledgeGraphLayerConfig, resolve_knowledge_graphs_config
from .constants import (
    CAPABILITY_EXPLAIN,
    CAPABILITY_NEIGHBORHOOD,
    CAPABILITY_PATH,
    CAPABILITY_QUERY,
    CAPABILITY_REFRESH,
    EVENT_REFRESH_COMPLETED,
    EVENT_REFRESH_FAILED,
    EVENT_REFRESH_STARTED,
    LAYER_SECOND_BRAIN,
    LAYER_THIRD_BRAIN,
)
from .errors import (
    DuplicateProviderError,
    UnknownProviderError,
    UnsupportedCapabilityError,
)
from .interfaces import KnowledgeGraphSource
from .models import (
    GraphExplainRequest,
    GraphExplainResult,
    GraphNeighborhoodRequest,
    GraphPathRequest,
    GraphPathResult,
    GraphQueryRequest,
    GraphQueryResult,
    GraphRefreshRequest,
    GraphRefreshResult,
    KnowledgeGraphHealth,
)
from .registry import KnowledgeGraphRegistry


@dataclass(frozen=True)
class KnowledgeGraphService:
    """Configured active graph-context providers.

    This service coordinates OpenMinion provider adapters. It does not own the
    Sophiagraph or PragmaGraph package engines.
    """

    sources: Mapping[str, KnowledgeGraphSource]
    emit_event: Callable[[str, Mapping[str, str]], None] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", dict(self.sources or {}))

    def _emit(self, event_type: str, payload: Mapping[str, str]) -> None:
        if self.emit_event is None:
            return
        self.emit_event(event_type, dict(payload))

    def list_sources(
        self, *, layer: str | None = None
    ) -> tuple[KnowledgeGraphSource, ...]:
        values = tuple(self.sources[name] for name in sorted(self.sources))
        if layer is None:
            return values
        return tuple(source for source in values if source.layer == layer)

    def get_source(self, name: str) -> KnowledgeGraphSource:
        key = str(name or "").strip()
        try:
            return self.sources[key]
        except KeyError as exc:
            raise UnknownProviderError(
                f"No active knowledge-graph source named {name!r}",
                details={"name": key, "active": sorted(self.sources)},
            ) from exc

    def _select_sources(
        self,
        *,
        provider_names: Iterable[str] | None = None,
        layer: str | None = None,
    ) -> tuple[KnowledgeGraphSource, ...]:
        selected = (
            tuple(self.get_source(name) for name in provider_names)
            if provider_names is not None
            else self.list_sources(layer=layer)
        )
        if layer is None:
            return selected
        return tuple(source for source in selected if source.layer == layer)

    @staticmethod
    def _ensure_capability(source: KnowledgeGraphSource, capability: str) -> None:
        if source.capabilities.supports(capability):
            return
        raise UnsupportedCapabilityError(
            f"Provider {source.name!r} does not advertise {capability}",
            details={"provider": source.name, "capability": capability},
        )

    def health(
        self,
        *,
        provider_names: Iterable[str] | None = None,
        layer: str | None = None,
    ) -> tuple[KnowledgeGraphHealth, ...]:
        return tuple(
            source.health()
            for source in self._select_sources(
                provider_names=provider_names,
                layer=layer,
            )
        )

    def query(
        self,
        request: GraphQueryRequest,
        *,
        provider_names: Iterable[str] | None = None,
        layer: str | None = None,
    ) -> tuple[GraphQueryResult, ...]:
        results: list[GraphQueryResult] = []
        for source in self._select_sources(provider_names=provider_names, layer=layer):
            self._ensure_capability(source, CAPABILITY_QUERY)
            results.append(source.query(request))
        return tuple(results)

    def neighborhood(
        self,
        request: GraphNeighborhoodRequest,
        *,
        provider_names: Iterable[str] | None = None,
        layer: str | None = None,
    ) -> tuple[GraphQueryResult, ...]:
        results: list[GraphQueryResult] = []
        for source in self._select_sources(provider_names=provider_names, layer=layer):
            self._ensure_capability(source, CAPABILITY_NEIGHBORHOOD)
            results.append(source.neighborhood(request))
        return tuple(results)

    def path(
        self,
        request: GraphPathRequest,
        *,
        provider_names: Iterable[str] | None = None,
        layer: str | None = None,
    ) -> tuple[GraphPathResult, ...]:
        results: list[GraphPathResult] = []
        for source in self._select_sources(provider_names=provider_names, layer=layer):
            self._ensure_capability(source, CAPABILITY_PATH)
            results.append(source.path(request))
        return tuple(results)

    def explain(
        self,
        request: GraphExplainRequest,
        *,
        provider_names: Iterable[str] | None = None,
        layer: str | None = None,
    ) -> tuple[GraphExplainResult, ...]:
        results: list[GraphExplainResult] = []
        for source in self._select_sources(provider_names=provider_names, layer=layer):
            self._ensure_capability(source, CAPABILITY_EXPLAIN)
            results.append(source.explain(request))
        return tuple(results)

    def refresh(
        self,
        request: GraphRefreshRequest,
        *,
        provider_names: Iterable[str] | None = None,
        layer: str | None = None,
    ) -> tuple[GraphRefreshResult, ...]:
        results: list[GraphRefreshResult] = []
        for source in self._select_sources(provider_names=provider_names, layer=layer):
            payload = {"provider": source.name, "layer": source.layer}
            self._emit(EVENT_REFRESH_STARTED, payload)
            try:
                self._ensure_capability(source, CAPABILITY_REFRESH)
                result = source.refresh(request)
            except Exception as exc:
                self._emit(
                    EVENT_REFRESH_FAILED,
                    {
                        **payload,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                raise
            results.append(result)
            self._emit(
                EVENT_REFRESH_COMPLETED,
                {**payload, "ok": str(result.ok).lower()},
            )
        return tuple(results)


def build_knowledge_graph_service(
    config_or_payload: object | None,
    *,
    registry: KnowledgeGraphRegistry,
) -> KnowledgeGraphService:
    """Instantiate active knowledge-graph sources through the registry."""
    config = resolve_knowledge_graphs_config(config_or_payload)
    sources: dict[str, KnowledgeGraphSource] = {}
    for layer_config in (config.second_brain, config.provider):
        _instantiate_layer_sources(layer_config, registry=registry, sources=sources)
    return KnowledgeGraphService(sources=sources)


def _instantiate_layer_sources(
    layer_config: KnowledgeGraphLayerConfig,
    *,
    registry: KnowledgeGraphRegistry,
    sources: dict[str, KnowledgeGraphSource],
) -> None:
    for active_name in layer_config.active:
        provider_config = layer_config.providers.get(active_name)
        if provider_config is None:
            raise UnknownProviderError(
                f"Active {layer_config.layer} provider {active_name!r} has no config",
                details={
                    "layer": layer_config.layer,
                    "name": active_name,
                    "configured": sorted(layer_config.providers),
                },
            )
        source = registry.instantiate(provider_config, layer=layer_config.layer)
        if source.name in sources:
            raise DuplicateProviderError(
                f"Duplicate active knowledge-graph source name {source.name!r}",
                details={"name": source.name},
            )
        sources[source.name] = source


def empty_knowledge_graph_service() -> KnowledgeGraphService:
    """Return an empty service for runtimes with no active graph providers."""
    return KnowledgeGraphService(sources={})


__all__ = [
    "KnowledgeGraphService",
    "build_knowledge_graph_service",
    "empty_knowledge_graph_service",
    "LAYER_SECOND_BRAIN",
    "LAYER_THIRD_BRAIN",
]
