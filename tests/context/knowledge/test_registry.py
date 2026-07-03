from __future__ import annotations

import pytest

from openminion.modules.context.knowledge import (
    CAPABILITY_CITATIONS,
    CAPABILITY_DURABLE_MEMORY,
    CAPABILITY_PATH,
    CAPABILITY_PROMOTES_TO_DURABLE,
    CAPABILITY_PROVENANCE,
    CAPABILITY_QUERY,
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
    KnowledgeGraphProviderConfig,
    KnowledgeGraphRegistry,
    LAYER_SECOND_BRAIN,
    LAYER_THIRD_BRAIN,
    TAG_CODE_GRAPH,
    TAG_HYBRID_GRAPH,
    report_optional_capabilities,
    validate_provider_capabilities,
)
from openminion.modules.context.knowledge.errors import (
    DisabledProviderError,
    DuplicateProviderError,
    HybridDurableMemoryError,
    MissingRequiredCapabilityError,
    UnknownProviderError,
)


class _FakeSource:
    def __init__(
        self,
        *,
        name: str,
        layer: str,
        capabilities: tuple[str, ...] = (CAPABILITY_QUERY,),
        tags: tuple[str, ...] = (),
    ) -> None:
        self._name = name
        self._layer = layer
        self._tags = tags
        self._caps = KnowledgeGraphCapabilities(advertised=capabilities)

    contract_version = "v1"

    @property
    def name(self) -> str:
        return self._name

    @property
    def layer(self) -> str:
        return self._layer

    @property
    def tags(self) -> tuple[str, ...]:
        return self._tags

    @property
    def capabilities(self) -> KnowledgeGraphCapabilities:
        return self._caps

    def health(self) -> KnowledgeGraphHealth:
        return KnowledgeGraphHealth(provider=self._name, layer=self._layer, ok=True)

    def query(self, request: GraphQueryRequest) -> GraphQueryResult:
        return GraphQueryResult(provider=self._name, layer=self._layer)

    def neighborhood(self, request: GraphNeighborhoodRequest) -> GraphQueryResult:
        return GraphQueryResult(provider=self._name, layer=self._layer)

    def path(self, request: GraphPathRequest) -> GraphPathResult:
        return GraphPathResult(provider=self._name, layer=self._layer)

    def explain(self, request: GraphExplainRequest) -> GraphExplainResult:
        return GraphExplainResult(
            provider=self._name,
            layer=self._layer,
            target_id=request.target_id,
        )

    def refresh(self, request: GraphRefreshRequest) -> GraphRefreshResult:
        return GraphRefreshResult(provider=self._name, layer=self._layer, ok=True)


def _make_factory(**source_kwargs: object):
    def _factory(
        *, config: KnowledgeGraphProviderConfig, layer: str, **_: object
    ) -> _FakeSource:
        kwargs = dict(source_kwargs)
        kwargs.setdefault("name", config.name)
        kwargs["layer"] = layer
        return _FakeSource(**kwargs)  # type: ignore[arg-type]

    return _factory


def test_register_and_lookup():
    registry = KnowledgeGraphRegistry()
    registry.register("graphify", _make_factory(capabilities=(CAPABILITY_QUERY,)))
    assert registry.is_registered("graphify")
    assert registry.list_registered() == ("graphify",)
    assert callable(registry.get("graphify"))


def test_register_normalizes_provider_name():
    registry = KnowledgeGraphRegistry()
    registry.register("  Graphify  ", _make_factory())
    assert registry.is_registered("graphify")
    assert "graphify" in registry.list_registered()


def test_duplicate_registration_raises():
    registry = KnowledgeGraphRegistry()
    registry.register("graphify", _make_factory())
    with pytest.raises(DuplicateProviderError) as exc_info:
        registry.register("graphify", _make_factory())
    assert exc_info.value.code == "DUPLICATE_PROVIDER"


def test_duplicate_registration_with_overwrite_succeeds():
    registry = KnowledgeGraphRegistry()
    registry.register("graphify", _make_factory(capabilities=(CAPABILITY_QUERY,)))
    new_factory = _make_factory(capabilities=(CAPABILITY_QUERY, CAPABILITY_CITATIONS))
    registry.register("graphify", new_factory, overwrite=True)
    assert registry.get("graphify") is new_factory


def test_unknown_provider_raises_typed_error():
    registry = KnowledgeGraphRegistry()
    with pytest.raises(UnknownProviderError) as exc_info:
        registry.get("does_not_exist")
    assert exc_info.value.code == "UNKNOWN_PROVIDER"
    assert exc_info.value.details["provider"] == "does_not_exist"


def test_resolve_disabled_provider_raises():
    registry = KnowledgeGraphRegistry()
    registry.register("graphify", _make_factory())
    cfg = KnowledgeGraphProviderConfig(
        name="repo_graph",
        provider="graphify",
        enabled=False,
    )
    with pytest.raises(DisabledProviderError) as exc_info:
        registry.resolve(cfg)
    assert exc_info.value.code == "DISABLED_PROVIDER"


def test_resolve_enabled_provider_returns_resolved_entry():
    registry = KnowledgeGraphRegistry()
    factory = _make_factory(capabilities=(CAPABILITY_QUERY, CAPABILITY_CITATIONS))
    registry.register("graphify", factory)
    cfg = KnowledgeGraphProviderConfig(name="repo_graph", provider="graphify")
    resolved = registry.resolve(cfg)
    assert resolved.name == "repo_graph"
    assert resolved.provider == "graphify"
    assert resolved.factory is factory
    assert resolved.config is cfg


def test_instantiate_validates_required_capabilities():
    registry = KnowledgeGraphRegistry()
    registry.register(
        "graphify",
        _make_factory(capabilities=(CAPABILITY_QUERY,)),
    )
    cfg = KnowledgeGraphProviderConfig(
        name="repo_graph",
        provider="graphify",
        required_capabilities=(CAPABILITY_QUERY, CAPABILITY_CITATIONS),
    )
    with pytest.raises(MissingRequiredCapabilityError) as exc_info:
        registry.instantiate(cfg, layer=LAYER_THIRD_BRAIN)
    assert exc_info.value.code == "MISSING_REQUIRED_CAPABILITY"
    assert CAPABILITY_CITATIONS in exc_info.value.details["missing"]


def test_instantiate_rejects_third_brain_advertising_durable_memory():
    registry = KnowledgeGraphRegistry()
    registry.register(
        "graphify",
        _make_factory(capabilities=(CAPABILITY_QUERY, CAPABILITY_DURABLE_MEMORY)),
    )
    cfg = KnowledgeGraphProviderConfig(
        name="repo_graph",
        provider="graphify",
        tags=(TAG_CODE_GRAPH,),
    )
    with pytest.raises(HybridDurableMemoryError) as exc_info:
        registry.instantiate(cfg, layer=LAYER_THIRD_BRAIN)
    assert exc_info.value.code == "HYBRID_DURABLE_MEMORY_REJECTED"


def test_instantiate_allows_second_brain_advertising_durable_memory():
    registry = KnowledgeGraphRegistry()
    registry.register(
        "sophiagraph",
        _make_factory(capabilities=(CAPABILITY_QUERY, CAPABILITY_DURABLE_MEMORY)),
    )
    cfg = KnowledgeGraphProviderConfig(name="sophiagraph", provider="sophiagraph")
    source = registry.instantiate(cfg, layer=LAYER_SECOND_BRAIN)
    assert source.layer == LAYER_SECOND_BRAIN
    assert CAPABILITY_DURABLE_MEMORY in source.capabilities.advertised


def test_instantiate_allows_third_brain_hybrid_with_promotes_to_durable():
    registry = KnowledgeGraphRegistry()
    registry.register(
        "graphify_sophiagraph_hybrid",
        _make_factory(
            capabilities=(
                CAPABILITY_QUERY,
                CAPABILITY_CITATIONS,
                CAPABILITY_PROMOTES_TO_DURABLE,
            )
        ),
    )
    cfg = KnowledgeGraphProviderConfig(
        name="hybrid_repo_graph",
        provider="graphify_sophiagraph_hybrid",
        tags=(TAG_HYBRID_GRAPH,),
        required_capabilities=(CAPABILITY_QUERY, CAPABILITY_PROMOTES_TO_DURABLE),
    )
    source = registry.instantiate(cfg, layer=LAYER_THIRD_BRAIN)
    assert CAPABILITY_PROMOTES_TO_DURABLE in source.capabilities.advertised
    assert CAPABILITY_DURABLE_MEMORY not in source.capabilities.advertised


def test_optional_capability_reporting():
    registry = KnowledgeGraphRegistry()
    registry.register(
        "graphify",
        _make_factory(capabilities=(CAPABILITY_QUERY, CAPABILITY_PATH)),
    )
    cfg = KnowledgeGraphProviderConfig(
        name="repo_graph",
        provider="graphify",
        required_capabilities=(CAPABILITY_QUERY,),
        optional_capabilities=(CAPABILITY_PATH, CAPABILITY_PROVENANCE),
    )
    source = registry.instantiate(cfg, layer=LAYER_THIRD_BRAIN)
    report = report_optional_capabilities(source, cfg)
    assert report == {CAPABILITY_PATH: True, CAPABILITY_PROVENANCE: False}


def test_validate_provider_capabilities_standalone():
    source = _FakeSource(
        name="x",
        layer=LAYER_SECOND_BRAIN,
        capabilities=(CAPABILITY_QUERY,),
    )
    cfg = KnowledgeGraphProviderConfig(
        name="x",
        provider="x",
        required_capabilities=(CAPABILITY_QUERY,),
    )
    validate_provider_capabilities(source, cfg, layer=LAYER_SECOND_BRAIN)  # no raise


def test_two_registries_are_independent():
    a = KnowledgeGraphRegistry()
    b = KnowledgeGraphRegistry()
    a.register("graphify", _make_factory())
    assert a.is_registered("graphify")
    assert not b.is_registered("graphify")


def test_empty_provider_name_raises():
    registry = KnowledgeGraphRegistry()
    with pytest.raises(UnknownProviderError):
        registry.register("   ", _make_factory())


def test_registry_does_not_import_provider_sdks_at_module_load():
    import openminion.modules.context.knowledge.registry as registry_mod

    code = registry_mod.__loader__.get_data(registry_mod.__file__).decode()  # type: ignore[union-attr]
    forbidden = ("graphify", "sophiagraph", "neo4j")
    for name in forbidden:
        assert f"import {name}" not in code, name
        assert f"from {name}" not in code, name
