from __future__ import annotations

from typing import Any, Mapping
from unittest import mock

import pytest

from openminion.tools.search.providers import (
    SearchProviderRegistry,
    _is_provider,
    provider_registry,
    register_provider,
)


class _FakeSearchProvider:
    provider_id = "fake_search_provider_sep03"
    display_name = "Fake Search Provider (SEP-03 test)"

    def search(
        self,
        query: str,
        *,
        max_results: int,
        args: Mapping[str, Any],
        ctx: Any,
    ) -> Mapping[str, Any]:
        return {"ok": True, "content": f"fake:{query}", "verified": False}

    def healthcheck(self, ctx: Any | None = None) -> bool:
        return True


def _reset_registry(registry: SearchProviderRegistry) -> None:
    registry._providers.clear()  # noqa: SLF001
    registry._provider_order.clear()  # noqa: SLF001
    registry._loaded_entry_points.clear()  # noqa: SLF001


def test_is_provider_helper_accepts_search_shape() -> None:
    assert _is_provider(_FakeSearchProvider()) is True

    class _NoSearch:
        provider_id = "x"

    assert _is_provider(_NoSearch()) is False

    class _NoId:
        def search(self, *a: Any, **k: Any) -> Mapping[str, Any]:  # noqa: D401
            return {}

    assert _is_provider(_NoId()) is False
    assert _is_provider(None) is False


def test_register_provider_is_idempotent() -> None:
    registry = SearchProviderRegistry()
    p = _FakeSearchProvider()
    registry.register(p)
    registry.register(p)
    assert registry.list_provider_ids() == ["fake_search_provider_sep03"]


def test_register_provider_rejects_missing_provider_id() -> None:
    registry = SearchProviderRegistry()

    class _Bad:
        provider_id = ""

        def search(self, *a: Any, **k: Any) -> Mapping[str, Any]:
            return {}

    with pytest.raises(ValueError):
        registry.register(_Bad())  # type: ignore[arg-type]


def test_load_entry_points_calls_register_search_provider_hook() -> None:
    registry = SearchProviderRegistry()

    captured: list[SearchProviderRegistry] = []

    def fake_register_hook(reg: SearchProviderRegistry) -> None:
        captured.append(reg)
        reg.register(_FakeSearchProvider())

    fake_target = type(
        "FakeTarget", (), {"register_search_provider": staticmethod(fake_register_hook)}
    )

    fake_ep = mock.MagicMock()
    fake_ep.name = "fake_search"
    fake_ep.value = "fake.module:register_search_provider"
    fake_ep.load.return_value = fake_target

    with mock.patch(
        "openminion.tools.search.providers._iter_entry_points",
        return_value=[fake_ep],
    ):
        loaded = registry.load_entry_points()

    assert loaded == ["fake_search"]
    assert captured and captured[0] is registry
    assert "fake_search_provider_sep03" in registry.list_provider_ids()


def test_load_entry_points_accepts_provider_object_target() -> None:
    registry = SearchProviderRegistry()

    fake_ep = mock.MagicMock()
    fake_ep.name = "fake_provider_obj"
    fake_ep.value = "fake.module:provider"
    fake_ep.load.return_value = _FakeSearchProvider()

    with mock.patch(
        "openminion.tools.search.providers._iter_entry_points",
        return_value=[fake_ep],
    ):
        loaded = registry.load_entry_points()

    assert loaded == ["fake_provider_obj"]
    assert "fake_search_provider_sep03" in registry.list_provider_ids()


def test_load_entry_points_accepts_provider_attribute_on_target() -> None:
    registry = SearchProviderRegistry()

    class _TargetWithProviderAttr:
        provider = _FakeSearchProvider()

    fake_ep = mock.MagicMock()
    fake_ep.name = "fake_target_with_provider"
    fake_ep.value = "fake.module:target"
    fake_ep.load.return_value = _TargetWithProviderAttr

    with mock.patch(
        "openminion.tools.search.providers._iter_entry_points",
        return_value=[fake_ep],
    ):
        loaded = registry.load_entry_points()

    assert loaded == ["fake_target_with_provider"]
    assert "fake_search_provider_sep03" in registry.list_provider_ids()


def test_load_entry_points_skips_stale_entry_points() -> None:
    registry = SearchProviderRegistry()

    fake_ep = mock.MagicMock()
    fake_ep.name = "stale_provider"
    fake_ep.value = "missing.module:does_not_exist"
    fake_ep.load.side_effect = ModuleNotFoundError("missing.module")

    with mock.patch(
        "openminion.tools.search.providers._iter_entry_points",
        return_value=[fake_ep],
    ):
        loaded = registry.load_entry_points()

    assert loaded == []
    assert registry.list_provider_ids() == []


def test_load_entry_points_raises_typeerror_on_malformed_target() -> None:
    registry = SearchProviderRegistry()

    fake_ep = mock.MagicMock()
    fake_ep.name = "malformed"
    fake_ep.value = "bad.module:bad_target"
    fake_ep.load.return_value = object()  # not a provider, no hook

    with mock.patch(
        "openminion.tools.search.providers._iter_entry_points",
        return_value=[fake_ep],
    ):
        with pytest.raises(TypeError) as excinfo:
            registry.load_entry_points()
    assert "malformed" in str(excinfo.value)


def test_load_entry_points_is_dedup_safe() -> None:
    registry = SearchProviderRegistry()

    fake_ep = mock.MagicMock()
    fake_ep.name = "dedup_provider"
    fake_ep.value = "fake.module:dedup"
    fake_ep.load.return_value = _FakeSearchProvider()

    with mock.patch(
        "openminion.tools.search.providers._iter_entry_points",
        return_value=[fake_ep],
    ):
        first = registry.load_entry_points()
        second = registry.load_entry_points()

    assert first == ["dedup_provider"]
    assert second == []
    assert registry.list_provider_ids().count("fake_search_provider_sep03") == 1


def test_load_entry_points_is_deterministic() -> None:
    registry = SearchProviderRegistry()

    class _ProviderA:
        provider_id = "ent_a"
        display_name = "Ent A"

        def search(self, *a: Any, **k: Any) -> Mapping[str, Any]:
            return {}

        def healthcheck(self, ctx: Any | None = None) -> bool:
            return True

    class _ProviderZ:
        provider_id = "ent_z"
        display_name = "Ent Z"

        def search(self, *a: Any, **k: Any) -> Mapping[str, Any]:
            return {}

        def healthcheck(self, ctx: Any | None = None) -> bool:
            return True

    ep_z = mock.MagicMock()
    ep_z.name = "z_provider"
    ep_z.value = "z.module:p"
    ep_z.load.return_value = _ProviderZ()

    ep_a = mock.MagicMock()
    ep_a.name = "a_provider"
    ep_a.value = "a.module:p"
    ep_a.load.return_value = _ProviderA()

    with mock.patch(
        "openminion.tools.search.providers._iter_entry_points",
        return_value=[ep_a, ep_z],
    ):
        loaded = registry.load_entry_points()
    assert loaded == ["a_provider", "z_provider"]
    assert registry.list_provider_ids() == ["ent_a", "ent_z"]


def test_external_provider_does_not_add_static_tool_lane() -> None:
    from openminion.modules.tool.registry import ToolRegistry
    from openminion.tools.search import plugin as search_plugin

    shared = provider_registry()
    saved_providers = dict(shared._providers)  # noqa: SLF001
    saved_order = list(shared._provider_order)  # noqa: SLF001
    saved_loaded_eps = set(shared._loaded_entry_points)  # noqa: SLF001
    _reset_registry(shared)

    try:
        fake_ep = mock.MagicMock()
        fake_ep.name = "external_no_static_lane"
        fake_ep.value = "external.module:provider"
        fake_ep.load.return_value = _FakeSearchProvider()

        with mock.patch(
            "openminion.tools.search.providers._iter_entry_points",
            return_value=[fake_ep],
        ):
            tool_registry = ToolRegistry()
            search_plugin.register(tool_registry)

        assert "fake_search_provider_sep03" in shared.list_provider_ids()

        tool_names = set(tool_registry.list().keys())
        assert "search.dispatch" in tool_names
        assert "search.tavily.search" in tool_names
        assert "search.brave.search" in tool_names
        assert "search.serpapi.search" in tool_names
        assert "search.firecrawl.search" in tool_names
        assert "search.serper.search" in tool_names
        assert "search.tinyfish.search" in tool_names
        assert "search.fake_search_provider_sep03.search" not in tool_names
    finally:
        _reset_registry(shared)
        shared._providers.update(saved_providers)  # noqa: SLF001
        shared._provider_order.extend(saved_order)  # noqa: SLF001
        shared._loaded_entry_points.update(saved_loaded_eps)  # noqa: SLF001


def test_module_level_register_provider_round_trips_through_registry() -> None:
    shared = provider_registry()
    saved_providers = dict(shared._providers)  # noqa: SLF001
    saved_order = list(shared._provider_order)  # noqa: SLF001
    _reset_registry(shared)

    try:
        register_provider(_FakeSearchProvider())
        assert "fake_search_provider_sep03" in shared.list_provider_ids()
    finally:
        _reset_registry(shared)
        shared._providers.update(saved_providers)  # noqa: SLF001
        shared._provider_order.extend(saved_order)  # noqa: SLF001
