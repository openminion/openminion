from __future__ import annotations

import pytest

from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.weather.providers import provider_registry
from openminion.tools.weather.providers.weatherapi.registrar import (
    REGISTRAR,
    WeatherApiRegistrar,
)


@pytest.fixture(autouse=True)
def _isolated_weather_registry():
    registry = provider_registry()
    providers = dict(registry._providers)
    order = list(registry._order)
    yield
    registry._providers = providers
    registry._order = order


def test_registrar_module_id() -> None:
    assert REGISTRAR.module_id == "weather.weatherapi"


def test_registrar_is_provider_only() -> None:
    assert REGISTRAR.is_provider_only is True


def test_registrar_get_manifest_returns_none() -> None:
    assert REGISTRAR.get_manifest(None) is None


def test_registrar_class_attributes() -> None:
    r = WeatherApiRegistrar()
    assert r.module_id == "weather.weatherapi"
    assert r.is_provider_only is True


def test_register_does_not_add_standalone_tool() -> None:
    tool_registry = ToolRegistry()
    REGISTRAR.register(tool_registry)

    names = set(tool_registry.list().keys())
    assert "weather.weatherapi" not in names
    assert "weather.weatherapi.current" not in names
    assert "weather" not in names


def test_register_adds_weatherapi_provider_to_facade() -> None:
    tool_registry = ToolRegistry()
    REGISTRAR.register(tool_registry)

    registry = provider_registry()
    provider = registry.get("weatherapi")
    assert provider is not None
    assert provider.provider_id == "weatherapi"


def test_register_idempotent_provider_id() -> None:
    tool_registry = ToolRegistry()
    REGISTRAR.register(tool_registry)
    REGISTRAR.register(tool_registry)

    registry = provider_registry()
    order = list(registry.list_provider_ids())
    assert order.count("weatherapi") == 1


def test_openmeteo_registers_before_weatherapi_in_default_order() -> None:
    from openminion.tools.weather.providers.openmeteo.registrar import (
        REGISTRAR as OPENMETEO_REG,
    )

    tool_registry = ToolRegistry()
    OPENMETEO_REG.register(tool_registry)
    REGISTRAR.register(tool_registry)

    ids = list(provider_registry().list_provider_ids())
    assert "openmeteo" in ids
    assert "weatherapi" in ids
    openmeteo_idx = ids.index("openmeteo")
    weatherapi_idx = ids.index("weatherapi")
    assert openmeteo_idx < weatherapi_idx, (
        f"openmeteo ({openmeteo_idx}) must come before weatherapi ({weatherapi_idx})"
    )
