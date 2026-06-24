from __future__ import annotations

import io
import json
from email.message import Message
from pathlib import Path
from urllib import error as urllib_error

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime import RuntimeContext

from openminion.tools.weather.providers.openmeteo.plugin import (
    _h_weather_openmeteo_current,
    _normalize_query_key,
    register,
    _resolve_location_argument,
    _resolve_location_from_location_tool,
    _verify_weather_result,
)


def _ctx(tmp_path: Path, weather_cfg: dict | None = None) -> RuntimeContext:
    run_root = tmp_path / "run"
    run_root.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    tools_cfg: dict = {
        "allow_prefix": ["weather."],
        "deny_exact": [],
        "deny_prefix": [],
    }
    merged_weather_cfg: dict = {"caching": {"enabled": False}}
    if weather_cfg is not None:
        merged_weather_cfg = _deep_merge(merged_weather_cfg, weather_cfg)
    tools_cfg["weather_openmeteo"] = merged_weather_cfg

    policy = Policy(
        raw={
            "workspace_root": str(tmp_path / "runs"),
            "tools": tools_cfg,
            "paths": {
                "read_allow": [str(workspace)],
                "write_allow": [str(workspace)],
                "deny": [],
            },
            "commands": {"mode": "allowlist", "allow": ["echo"]},
        }
    )
    return RuntimeContext(
        policy=policy,
        workspace=workspace,
        run_root=run_root,
        scope="READ_ONLY",
        confirm=False,
    )


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
            continue
        out[key] = value
    return out


class _Response:
    def __init__(self, payload, headers=None):
        self._payload = payload
        self.headers = headers or Message()

    def read(self) -> bytes:
        if isinstance(self._payload, (dict, list)):
            return json.dumps(self._payload).encode("utf-8")
        return str(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _headers(values: dict[str, str]) -> Message:
    msg = Message()
    for key, value in values.items():
        msg[key] = value
    return msg


def test_plugin_does_not_register_standalone_tool():
    registry = ToolRegistry()
    register(registry)

    names = set(registry.list().keys())
    assert "weather.openmeteo.current" not in names


def test_location_alias_resolution_prefers_first_non_empty():
    query = _resolve_location_argument(
        {"location": "", "city": "", "query": "Tokyo", "place": "Japan"}
    )
    assert query == "Tokyo"


def test_location_alias_resolution_ignores_literal_none_tokens():
    query = _resolve_location_argument(
        {"location": "None", "city": "null", "query": "Tokyo", "place": "Japan"}
    )
    assert query == "Tokyo"


def test_location_tool_fallback_ignores_literal_none_tokens(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "openminion.tools.location.plugin._h_get",
        lambda _args, _ctx: {
            "ok": True,
            "data": {
                "city": None,
                "region": "None",
                "country": "United States",
            },
        },
    )

    assert _resolve_location_from_location_tool(_ctx(tmp_path)) == "United States"


def test_weather_uses_location_tool_fallback_when_location_missing(
    monkeypatch, tmp_path
):
    geocode_payload = {
        "results": [
            {
                "name": "San Francisco",
                "country": "United States",
                "latitude": 37.7749,
                "longitude": -122.4194,
            }
        ]
    }
    forecast_payload = {
        "current": {
            "time": "2026-03-03T10:00",
            "temperature_2m": 14.5,
            "relative_humidity_2m": 76,
            "weather_code": 3,
            "wind_speed_10m": 9.1,
        }
    }

    def _fake_urlopen(request, timeout):
        del timeout
        if "geocoding-api.open-meteo.com" in request.full_url:
            return _Response(geocode_payload)
        if "api.open-meteo.com" in request.full_url:
            return _Response(forecast_payload)
        raise AssertionError(f"Unexpected URL: {request.full_url}")

    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.urllib_request.urlopen",
        _fake_urlopen,
    )
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin._resolve_location_from_location_tool",
        lambda _ctx: "San Francisco",
    )

    result = _h_weather_openmeteo_current({}, _ctx(tmp_path))
    assert result["location"]["resolved_name"] == "San Francisco"
    assert result["verified"] is True


def test_weather_does_not_use_location_tool_fallback_when_location_present(
    monkeypatch, tmp_path
):
    geocode_payload = {
        "results": [
            {
                "name": "Tokyo",
                "country": "Japan",
                "latitude": 35.6762,
                "longitude": 139.6503,
            }
        ]
    }
    forecast_payload = {
        "current": {
            "time": "2026-03-03T10:00",
            "temperature_2m": 14.5,
            "relative_humidity_2m": 76,
            "weather_code": 3,
            "wind_speed_10m": 9.1,
        }
    }

    def _fake_urlopen(request, timeout):
        del timeout
        if "geocoding-api.open-meteo.com" in request.full_url:
            return _Response(geocode_payload)
        if "api.open-meteo.com" in request.full_url:
            return _Response(forecast_payload)
        raise AssertionError(f"Unexpected URL: {request.full_url}")

    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.urllib_request.urlopen",
        _fake_urlopen,
    )
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin._resolve_location_from_location_tool",
        lambda _ctx: (_ for _ in ()).throw(
            AssertionError("location fallback should not run")
        ),
    )

    result = _h_weather_openmeteo_current({"location": "Tokyo"}, _ctx(tmp_path))
    assert result["location"]["resolved_name"] == "Tokyo"
    assert result["verified"] is True


def test_normalize_query_key_collapses_whitespace_and_case():
    assert _normalize_query_key("   San   Francisco ") == "san francisco"


def test_verify_requires_matching_location_numeric_metrics_and_observed_at():
    payload = {
        "location": {
            "query": "San Francisco",
            "resolved_name": "SF",
            "country": "United States",
            "latitude": 1.0,
            "longitude": 2.0,
        },
        "observed_at": "2026-03-03T01:02:03Z",
        "metrics": {
            "temperature_c": 10.0,
            "humidity_pct": 80.0,
            "wind_speed_kmh": 7.0,
            "weather_code": 2.0,
        },
    }
    assert _verify_weather_result(payload, expected_query="San Francisco") is True

    payload["metrics"]["wind_speed_kmh"] = "bad"
    assert _verify_weather_result(payload, expected_query="San Francisco") is False


def test_current_lookup_calls_open_meteo_and_shapes_payload(monkeypatch, tmp_path):
    geocode_payload = {
        "results": [
            {
                "name": "San Francisco",
                "country": "United States",
                "latitude": 37.7749,
                "longitude": -122.4194,
            }
        ]
    }
    forecast_payload = {
        "current": {
            "time": "2026-03-03T10:00",
            "temperature_2m": 14.5,
            "relative_humidity_2m": 76,
            "weather_code": 3,
            "wind_speed_10m": 9.1,
        }
    }

    def _fake_urlopen(request, timeout):
        del timeout
        if "geocoding-api.open-meteo.com" in request.full_url:
            return _Response(geocode_payload)
        if "api.open-meteo.com" in request.full_url:
            return _Response(forecast_payload)
        raise AssertionError(f"Unexpected URL: {request.full_url}")

    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.urllib_request.urlopen",
        _fake_urlopen,
    )

    ctx = _ctx(tmp_path, weather_cfg={"caching": {"enabled": True}})
    result = _h_weather_openmeteo_current({"location": "San Francisco"}, ctx)

    assert result["location"]["resolved_name"] == "San Francisco"
    assert result["location"]["country"] == "United States"
    assert result["metrics"]["temperature_c"] == 14.5
    assert result["metrics"]["humidity_pct"] == 76.0
    assert result["metrics"]["wind_speed_kmh"] == 9.1
    assert result["observed_at"] == "2026-03-03T10:00"
    assert result["source"]["provider"] == "open-meteo"
    assert len(result["source"]["endpoints"]) == 2
    assert result["verified"] is True


def test_current_lookup_accepts_lat_lon_without_geocoding(monkeypatch, tmp_path):
    forecast_payload = {
        "current": {
            "time": "2026-03-03T10:00",
            "temperature_2m": 14.5,
            "relative_humidity_2m": 76,
            "weather_code": 3,
            "wind_speed_10m": 9.1,
        }
    }

    def _fake_urlopen(request, timeout):
        del timeout
        if "geocoding-api.open-meteo.com" in request.full_url:
            raise AssertionError(
                "geocoding should not be called when lat/lon are provided"
            )
        if "api.open-meteo.com" in request.full_url:
            return _Response(forecast_payload)
        raise AssertionError(f"Unexpected URL: {request.full_url}")

    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.urllib_request.urlopen",
        _fake_urlopen,
    )

    ctx = _ctx(tmp_path, weather_cfg={"caching": {"enabled": True}})
    result = _h_weather_openmeteo_current({"lat": 37.7749, "lon": -122.4194}, ctx)

    assert result["location"]["latitude"] == pytest.approx(37.7749)
    assert result["location"]["longitude"] == pytest.approx(-122.4194)
    assert result["metrics"]["temperature_c"] == 14.5
    assert result["source"]["endpoints"][0].startswith("coordinates:")
    assert result["verified"] is True


def test_current_lookup_rejects_partial_coordinates(tmp_path):
    ctx = _ctx(tmp_path, weather_cfg={"caching": {"enabled": False}})
    with pytest.raises(Exception) as exc:
        _h_weather_openmeteo_current({"lat": 37.7749}, ctx)
    assert "latitude and longitude are required" in str(exc.value).lower()


def test_cache_reuses_fresh_lookup(monkeypatch, tmp_path):
    calls = {"count": 0}
    from openminion.tools.weather.providers.openmeteo import plugin as weather_plugin

    weather_plugin._CACHE.clear()

    def _fake_urlopen(request, timeout):
        del timeout
        calls["count"] += 1
        if "geocoding-api.open-meteo.com" in request.full_url:
            return _Response(
                {
                    "results": [
                        {
                            "name": "Tokyo",
                            "country": "Japan",
                            "latitude": 35.6762,
                            "longitude": 139.6503,
                        }
                    ]
                }
            )
        return _Response(
            {
                "current": {
                    "time": "2026-03-03T18:00",
                    "temperature_2m": 9.2,
                    "relative_humidity_2m": 64,
                    "weather_code": 2,
                    "wind_speed_10m": 7.8,
                }
            }
        )

    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.urllib_request.urlopen",
        _fake_urlopen,
    )

    ctx = _ctx(tmp_path, weather_cfg={"caching": {"enabled": True}})
    first = _h_weather_openmeteo_current({"city": "Tokyo"}, ctx)
    second = _h_weather_openmeteo_current({"city": "Tokyo"}, ctx)

    assert first["location"]["resolved_name"] == "Tokyo"
    assert second["location"]["resolved_name"] == "Tokyo"
    assert calls["count"] == 2


def test_geocode_not_found_maps_to_not_found(monkeypatch, tmp_path):
    def _fake_urlopen(request, timeout):
        del timeout
        if "geocoding-api.open-meteo.com" in request.full_url:
            return _Response({"results": []})
        if "nominatim.openstreetmap.org" in request.full_url:
            return _Response([])
        raise AssertionError(f"Unexpected URL: {request.full_url}")

    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.urllib_request.urlopen",
        _fake_urlopen,
    )

    ctx = _ctx(tmp_path)
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_weather_openmeteo_current({"location": "Missing City"}, ctx)

    assert exc_info.value.code == "NOT_FOUND"
    assert exc_info.value.details["secondary_geocoder"] == "nominatim"
    assert "user_hint" in exc_info.value.details


def test_geocode_not_found_falls_back_to_secondary_geocoder(monkeypatch, tmp_path):
    forecast_payload = {
        "current": {
            "time": "2026-03-22T14:40",
            "temperature_2m": 16.4,
            "relative_humidity_2m": 100,
            "weather_code": 45,
            "wind_speed_10m": 4.8,
        }
    }

    def _fake_urlopen(request, timeout):
        del timeout
        if "geocoding-api.open-meteo.com" in request.full_url:
            return _Response({"results": []})
        if "nominatim.openstreetmap.org" in request.full_url:
            return _Response(
                [
                    {
                        "name": "San Francisco",
                        "display_name": "San Francisco, California, United States",
                        "lat": "37.7879363",
                        "lon": "-122.4075201",
                        "address": {"country": "United States"},
                    }
                ]
            )
        if "api.open-meteo.com" in request.full_url:
            return _Response(forecast_payload)
        raise AssertionError(f"Unexpected URL: {request.full_url}")

    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.urllib_request.urlopen",
        _fake_urlopen,
    )

    ctx = _ctx(tmp_path)
    result = _h_weather_openmeteo_current({"location": "sf"}, ctx)

    assert result["location"]["resolved_name"] == "San Francisco"
    assert result["location"]["country"] == "United States"
    assert result["source"]["provider"] == "open-meteo"
    assert result["source"]["geocoding_provider"] == "nominatim"
    assert "geocode_fallback_used:nominatim" in result["warnings"]
    assert result["verified"] is True


def test_rate_limited_maps_to_rate_limited(monkeypatch, tmp_path):
    def _fake_urlopen(request, timeout):
        del timeout
        raise urllib_error.HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            _headers({}),
            io.BytesIO(b'{"message":"limit"}'),
        )

    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.urllib_request.urlopen",
        _fake_urlopen,
    )

    ctx = _ctx(tmp_path, weather_cfg={"retries": 0})
    with pytest.raises(ToolRuntimeError) as exc_info:
        _h_weather_openmeteo_current({"location": "San Francisco"}, ctx)

    assert exc_info.value.code == "RATE_LIMITED"


def test_fallback_static_samples_when_enabled(monkeypatch, tmp_path):
    def _fake_urlopen(request, timeout):
        del timeout
        raise urllib_error.URLError("network down")

    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.urllib_request.urlopen",
        _fake_urlopen,
    )
    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.time.sleep",
        lambda _value: None,
    )

    ctx = _ctx(
        tmp_path,
        weather_cfg={
            "retries": 0,
            "fallback": {
                "enabled": True,
                "mode": "static_samples",
            },
        },
    )
    result = _h_weather_openmeteo_current({"location": "San Francisco"}, ctx)

    assert result["source"]["provider"] == "open-meteo"
    assert "fallback_used" in result["warnings"]
    assert "Fallback sample used" in result["source"]["license_note"]


def test_debug_artifacts_are_written_when_enabled(monkeypatch, tmp_path):
    class _FailIfCASUsed:
        def __init__(self) -> None:
            self.called = False

        def ingest_bytes(self, **kwargs):
            del kwargs
            self.called = True
            raise AssertionError("weather debug artifacts must stay runtime-local")

    def _fake_urlopen(request, timeout):
        del timeout
        if "geocoding-api.open-meteo.com" in request.full_url:
            return _Response(
                {
                    "results": [
                        {
                            "name": "Tokyo",
                            "country": "Japan",
                            "latitude": 35.6762,
                            "longitude": 139.6503,
                        }
                    ]
                }
            )
        return _Response(
            {
                "current": {
                    "time": "2026-03-03T18:00",
                    "temperature_2m": 9.2,
                    "relative_humidity_2m": 64,
                    "weather_code": 2,
                    "wind_speed_10m": 7.8,
                }
            }
        )

    monkeypatch.setattr(
        "openminion.tools.weather.providers.openmeteo.plugin.urllib_request.urlopen",
        _fake_urlopen,
    )

    ctx = _ctx(tmp_path)
    artifactctl = _FailIfCASUsed()
    ctx.artifactctl = artifactctl
    result = _h_weather_openmeteo_current({"location": "Tokyo", "debug": True}, ctx)

    refs = result.get("debug_artifacts", [])
    assert len(refs) == 2
    for rel in refs:
        assert (ctx.run_root / rel).exists()
        assert not str(rel).startswith("artifact://sha256/")
    assert artifactctl.called is False
