from __future__ import annotations

from openminion.modules.context.knowledge import (
    CAPABILITY_EXPLAIN,
    CAPABILITY_METHOD_MAP,
    CAPABILITY_NEIGHBORHOOD,
    CAPABILITY_PATH,
    CAPABILITY_QUERY,
    CAPABILITY_REFRESH,
    GraphExplainRequest,
    GraphExplainResult,
    GraphNeighborhoodRequest,
    GraphPathRequest,
    GraphPathResult,
    GraphQueryRequest,
    GraphQueryResult,
    GraphRefreshRequest,
    GraphRefreshResult,
    KnowledgeGraphCapabilities,
    KnowledgeGraphHealth,
    KnowledgeGraphSource,
    LAYER_SECOND_BRAIN,
)


class _MinimalProvider:
    contract_version = "v1"

    @property
    def name(self) -> str:
        return "minimal"

    @property
    def layer(self) -> str:
        return LAYER_SECOND_BRAIN

    @property
    def tags(self) -> tuple[str, ...]:
        return ()

    @property
    def capabilities(self) -> KnowledgeGraphCapabilities:
        return KnowledgeGraphCapabilities(advertised=[CAPABILITY_QUERY])

    def health(self) -> KnowledgeGraphHealth:
        return KnowledgeGraphHealth(
            provider="minimal",
            layer=LAYER_SECOND_BRAIN,
            ok=True,
        )

    def query(self, request: GraphQueryRequest) -> GraphQueryResult:
        return GraphQueryResult(provider="minimal", layer=LAYER_SECOND_BRAIN)

    def neighborhood(self, request: GraphNeighborhoodRequest) -> GraphQueryResult:
        return GraphQueryResult(provider="minimal", layer=LAYER_SECOND_BRAIN)

    def path(self, request: GraphPathRequest) -> GraphPathResult:
        return GraphPathResult(provider="minimal", layer=LAYER_SECOND_BRAIN)

    def explain(self, request: GraphExplainRequest) -> GraphExplainResult:
        return GraphExplainResult(
            provider="minimal",
            layer=LAYER_SECOND_BRAIN,
            target_id=request.target_id,
        )

    def refresh(self, request: GraphRefreshRequest) -> GraphRefreshResult:
        return GraphRefreshResult(
            provider="minimal",
            layer=LAYER_SECOND_BRAIN,
            ok=True,
        )


def test_minimal_provider_satisfies_protocol():
    provider = _MinimalProvider()
    assert isinstance(provider, KnowledgeGraphSource)


def test_capability_method_map_covers_capability_gated_methods():
    # Every method that can be capability-gated must have an entry.
    for capability in (
        CAPABILITY_QUERY,
        CAPABILITY_PATH,
        CAPABILITY_NEIGHBORHOOD,
        CAPABILITY_EXPLAIN,
        CAPABILITY_REFRESH,
    ):
        assert capability in CAPABILITY_METHOD_MAP, capability


def test_capability_method_map_values_match_protocol_methods():
    provider = _MinimalProvider()
    for method_name in CAPABILITY_METHOD_MAP.values():
        assert callable(getattr(provider, method_name)), method_name
