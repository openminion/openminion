from __future__ import annotations

from types import SimpleNamespace

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry

from openminion.tools.weather import register_provider
from openminion.tools.weather.plugin import _h_weather, _provider_chain, register
from openminion.tools.weather.providers import provider_registry


@pytest.fixture(autouse=True)
def _isolated_weather_registry():
    registry = provider_registry()
    providers = dict(registry._providers)
    order = list(registry._order)
    yield
    registry._providers = providers
    registry._order = order


def _ctx(*, runtime_tools: dict | None = None):
    raw: dict[str, object] = {}
    if runtime_tools is not None:
        raw["context_metadata"] = {"runtime_tools": runtime_tools}
    return SimpleNamespace(policy=SimpleNamespace(raw=raw))


class _CustomProvider:
    provider_id = "unit-test-weather-provider"

    def __init__(self) -> None:
        self.last_query_args: dict[str, object] | None = None
        self.last_extension_args: dict[str, object] | None = None

    def lookup(self, *, query_args, extension_args, ctx):
        del ctx
        self.last_query_args = dict(query_args)
        self.last_extension_args = dict(extension_args)
        return {
            "location": {
                "query": str(query_args.get("location", "")),
                "resolved_name": "Tokyo",
                "country": "Japan",
                "latitude": 35.67,
                "longitude": 139.65,
            },
            "observed_at": "2026-03-17T00:00:00Z",
            "metrics": {
                "temperature_c": 18.0,
                "humidity_pct": 71.0,
                "wind_speed_kmh": 8.0,
                "weather_code": 2.0,
            },
            "source": {
                "provider": "unit-test",
                "endpoints": [],
                "license_note": "n/a",
            },
            "verified": True,
        }

    def healthcheck(self) -> bool:
        return True


def test_register_adds_weather_core_tool() -> None:
    registry = ToolRegistry()
    register(registry)

    names = set(registry.list().keys())
    assert "weather" in names


def test_weather_normalizes_aliases_for_provider_call() -> None:
    provider = _CustomProvider()
    register_provider(provider)

    result = _h_weather(
        {
            "provider": provider.provider_id,
            "city": "Tokyo",
            "units": "metric",
            "language": "ja",
        },
        SimpleNamespace(),
    )

    assert provider.last_query_args == {
        "location": "Tokyo",
        "language": "ja",
        "debug": False,
    }
    assert provider.last_extension_args == {"units": "metric"}
    assert result["location"]["resolved_name"] == "Tokyo"


def test_weather_treats_literal_none_alias_as_missing() -> None:
    provider = _CustomProvider()
    register_provider(provider)

    result = _h_weather(
        {
            "provider": provider.provider_id,
            "location": "None",
            "query": "Tokyo",
        },
        SimpleNamespace(),
    )

    assert provider.last_query_args == {
        "location": "Tokyo",
        "debug": False,
    }
    assert result["location"]["resolved_name"] == "Tokyo"


def test_weather_requires_coordinate_pair() -> None:
    with pytest.raises(ToolRuntimeError, match="Both latitude and longitude"):
        _h_weather({"lat": 35.0}, SimpleNamespace())


def test_weather_legacy_fallback_provider_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _fake_openmeteo_handler(args, ctx):
        del ctx
        captured.update(dict(args or {}))
        return {
            "location": {
                "query": str(args.get("location", "")),
                "resolved_name": "Seoul",
                "country": "KR",
                "latitude": 37.56,
                "longitude": 126.97,
            },
            "observed_at": "2026-03-17T00:00:00Z",
            "metrics": {
                "temperature_c": 10.0,
                "humidity_pct": 50.0,
                "wind_speed_kmh": 5.0,
                "weather_code": 1.0,
            },
            "source": {
                "provider": "open-meteo",
                "endpoints": [],
                "license_note": "n/a",
            },
            "verified": True,
        }

    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin._h_weather_openmeteo_current",
        _fake_openmeteo_handler,
    )

    class _PatchedOpenMeteoProvider:
        provider_id = "openmeteo"

        def lookup(self, *, query_args, extension_args, ctx):
            del extension_args
            return _fake_openmeteo_handler(dict(query_args), ctx)

        def healthcheck(self) -> bool:
            return True

    register_provider(_PatchedOpenMeteoProvider())

    result = _h_weather(
        {"provider": "openmeteo", "location": "Seoul"}, SimpleNamespace()
    )

    assert captured["location"] == "Seoul"
    assert result["location"]["resolved_name"] == "Seoul"


def test_weather_compacts_provider_summary_for_judge_mode() -> None:
    provider = _CustomProvider()
    register_provider(provider)

    result = _h_weather(
        {"provider": provider.provider_id, "city": "Tokyo"},
        SimpleNamespace(),
    )

    assert result["summary"] == "Tokyo, Japan: 18.0°C, partly cloudy."


class _RoutingProvider:
    def __init__(self, provider_id: str, *, healthy: bool = True) -> None:
        self.provider_id = provider_id
        self.healthy = healthy
        self.calls = 0

    def lookup(self, *, query_args, extension_args, ctx):
        del extension_args, ctx
        self.calls += 1
        return {
            "location": {
                "query": str(query_args.get("location", "")),
                "resolved_name": str(query_args.get("location", "")),
                "country": "n/a",
                "latitude": 0.0,
                "longitude": 0.0,
            },
            "observed_at": "2026-03-17T00:00:00Z",
            "metrics": {
                "temperature_c": 0.0,
                "humidity_pct": 0.0,
                "wind_speed_kmh": 0.0,
                "weather_code": 0.0,
            },
            "source": {"provider": self.provider_id},
            "verified": True,
        }

    def healthcheck(self) -> bool:
        return self.healthy


def test_weather_runtime_tools_order_and_default_override_registry_order() -> None:
    alpha = _RoutingProvider("weather-alpha")
    bravo = _RoutingProvider("weather-bravo")
    register_provider(alpha)
    register_provider(bravo)

    result = _h_weather(
        {"location": "Tokyo"},
        _ctx(
            runtime_tools={
                "weather": {
                    "enabled_providers": ["weather-alpha", "weather-bravo"],
                    "default_provider": "weather-bravo",
                    "provider_order": ["weather-bravo", "weather-alpha"],
                    "allow_fallback": True,
                }
            }
        ),
    )

    assert result["source"]["provider_id"] == "weather-bravo"
    assert bravo.calls == 1
    assert alpha.calls == 0


def test_weather_explicit_provider_bypasses_runtime_order() -> None:
    alpha = _RoutingProvider("weather-explicit-alpha")
    bravo = _RoutingProvider("weather-explicit-bravo")
    register_provider(alpha)
    register_provider(bravo)

    result = _h_weather(
        {"provider": "weather-explicit-alpha", "location": "Tokyo"},
        _ctx(
            runtime_tools={
                "weather": {
                    "enabled_providers": [
                        "weather-explicit-alpha",
                        "weather-explicit-bravo",
                    ],
                    "default_provider": "weather-explicit-bravo",
                    "provider_order": [
                        "weather-explicit-bravo",
                        "weather-explicit-alpha",
                    ],
                    "allow_fallback": True,
                }
            }
        ),
    )

    assert result["source"]["provider_id"] == "weather-explicit-alpha"
    assert alpha.calls == 1
    assert bravo.calls == 0


def test_weather_runtime_tools_preserve_health_aware_fallback() -> None:
    unhealthy = _RoutingProvider("weather-unhealthy", healthy=False)
    healthy = _RoutingProvider("weather-healthy")
    register_provider(unhealthy)
    register_provider(healthy)

    result = _h_weather(
        {"location": "Osaka"},
        _ctx(
            runtime_tools={
                "weather": {
                    "enabled_providers": ["weather-unhealthy", "weather-healthy"],
                    "default_provider": "weather-unhealthy",
                    "provider_order": ["weather-unhealthy", "weather-healthy"],
                    "allow_fallback": True,
                }
            }
        ),
    )

    assert result["source"]["provider_id"] == "weather-healthy"
    assert "weather-unhealthy" in " ".join(result["warnings"])
    assert unhealthy.calls == 0
    assert healthy.calls == 1


def test_weather_no_config_keeps_registry_order() -> None:
    first = _RoutingProvider("weather-first")
    second = _RoutingProvider("weather-second")
    register_provider(first)
    register_provider(second)

    assert _provider_chain("auto", _ctx())[-2:] == ["weather-first", "weather-second"]


def test_explicit_weatherapi_provider_selection() -> None:
    from openminion.tools.weather.providers.weatherapi.registrar import (
        REGISTRAR as WA_REG,
    )

    registry = ToolRegistry()
    WA_REG.register(registry)

    wa_provider = provider_registry().get("weatherapi")
    assert wa_provider is not None

    captured: dict[str, object] = {}

    class _SpyWeatherApiProvider:
        provider_id = "weatherapi"

        def lookup(self, *, query_args, extension_args, ctx):
            del ctx
            captured["query_args"] = dict(query_args)
            return {
                "location": {
                    "query": str(query_args.get("location", "")),
                    "resolved_name": "Oakland",
                    "country": "United States",
                    "latitude": 37.80,
                    "longitude": -122.27,
                },
                "observed_at": "2026-03-31T10:00:00",
                "metrics": {
                    "temperature_c": 18.0,
                    "humidity_pct": 65.0,
                    "wind_speed_kmh": 12.0,
                    "weather_code": 1003.0,
                },
                "source": {"provider": "weatherapi"},
                "verified": True,
                "warnings": [],
            }

        def healthcheck(self) -> bool:
            return True

    registry2 = provider_registry()
    registry2._providers["weatherapi"] = _SpyWeatherApiProvider()

    result = _h_weather(
        {"provider": "weatherapi", "location": "Oakland, CA"},
        SimpleNamespace(),
    )

    assert captured.get("query_args", {}).get("location") == "Oakland, CA"
    assert result["source"]["provider_id"] == "weatherapi"


def test_default_provider_order_openmeteo_before_weatherapi() -> None:
    from openminion.tools.weather.providers.openmeteo.registrar import (
        REGISTRAR as OM_REG,
    )
    from openminion.tools.weather.providers.weatherapi.registrar import (
        REGISTRAR as WA_REG,
    )

    registry = ToolRegistry()
    OM_REG.register(registry)
    WA_REG.register(registry)

    ids = list(provider_registry().list_provider_ids())
    assert "openmeteo" in ids
    assert "weatherapi" in ids
    assert ids.index("openmeteo") < ids.index("weatherapi"), (
        f"openmeteo must come before weatherapi in default order, got: {ids}"
    )


def test_fallback_from_unhealthy_openmeteo_to_weatherapi() -> None:

    class _UnhealthyOpenMeteo:
        provider_id = "openmeteo"

        def lookup(self, *, query_args, extension_args, ctx):
            raise AssertionError("should not be called when unhealthy")

        def healthcheck(self) -> bool:
            return False

    class _HealthyWeatherApi:
        provider_id = "weatherapi"

        def lookup(self, *, query_args, extension_args, ctx):
            del extension_args, ctx
            return {
                "location": {
                    "query": str(query_args.get("location", "")),
                    "resolved_name": "Oakland",
                    "country": "United States",
                    "latitude": 37.80,
                    "longitude": -122.27,
                },
                "observed_at": "2026-03-31T10:00:00",
                "metrics": {
                    "temperature_c": 18.0,
                    "humidity_pct": 65.0,
                    "wind_speed_kmh": 12.0,
                    "weather_code": 1003.0,
                },
                "source": {"provider": "weatherapi"},
                "verified": True,
                "warnings": [],
            }

        def healthcheck(self) -> bool:
            return True

    register_provider(_UnhealthyOpenMeteo())
    register_provider(_HealthyWeatherApi())

    result = _h_weather(
        {"location": "Oakland, CA"},
        _ctx(
            runtime_tools={
                "weather": {
                    "enabled_providers": ["openmeteo", "weatherapi"],
                    "provider_order": ["openmeteo", "weatherapi"],
                    "allow_fallback": True,
                }
            }
        ),
    )

    assert result["source"]["provider_id"] == "weatherapi"
    assert any("openmeteo" in w for w in result.get("warnings", []))
