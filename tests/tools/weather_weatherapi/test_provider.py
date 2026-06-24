from __future__ import annotations

import io
import json
from email.message import Message
from types import SimpleNamespace
from urllib import error as urllib_error

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.weather.providers.weatherapi.config import (
    WeatherApiProviderConfig,
)
from openminion.tools.weather.providers.weatherapi.provider import (
    WeatherApiProvider,
    _normalize_response,
    _resolve_q,
)


_SAMPLE_PAYLOAD = {
    "location": {
        "name": "London",
        "region": "City of London, Greater London",
        "country": "United Kingdom",
        "lat": 51.52,
        "lon": -0.11,
        "localtime_epoch": 1711900000,
        "localtime": "2024-03-31 14:00",
    },
    "current": {
        "last_updated": "2024-03-31 13:45",
        "temp_c": 13.5,
        "feelslike_c": 11.2,
        "humidity": 72,
        "wind_kph": 20.1,
        "precip_mm": 0.0,
        "cloud": 75,
        "vis_km": 10.0,
        "uv": 3.0,
        "condition": {
            "text": "Partly cloudy",
            "code": 1003,
        },
    },
}


def _ctx_no_key() -> SimpleNamespace:
    return SimpleNamespace(env=SimpleNamespace(get=lambda name, default="": default))


def _http_error(status: int, body: bytes = b"") -> urllib_error.HTTPError:
    return urllib_error.HTTPError(
        "https://api.weatherapi.com/v1/current.json",
        status,
        "err",
        Message(),
        io.BytesIO(body),
    )


def test_resolve_q_from_location() -> None:
    assert _resolve_q({"location": "London"}) == "London"


def test_resolve_q_prefers_location_over_city() -> None:
    assert _resolve_q({"location": "London", "city": "Paris"}) == "London"


def test_resolve_q_from_city_when_location_absent() -> None:
    assert _resolve_q({"city": "Tokyo"}) == "Tokyo"


def test_resolve_q_from_query_alias() -> None:
    assert _resolve_q({"query": "Berlin"}) == "Berlin"


def test_resolve_q_ignores_literal_none_tokens() -> None:
    assert _resolve_q({"location": "None", "city": "Tokyo"}) == "Tokyo"


def test_resolve_q_from_place_alias() -> None:
    assert _resolve_q({"place": "Rome"}) == "Rome"


def test_resolve_q_from_lat_lon() -> None:
    result = _resolve_q({"latitude": 37.77, "longitude": -122.41})
    assert result == "37.77,-122.41"


def test_resolve_q_empty_when_nothing_provided() -> None:
    assert _resolve_q({}) == ""


def test_healthcheck_true_when_key_available() -> None:
    provider = WeatherApiProvider(WeatherApiProviderConfig(api_key="my-key"))
    assert provider.healthcheck() is True


def test_healthcheck_false_when_no_key() -> None:
    provider = WeatherApiProvider()
    assert provider.healthcheck(ctx=_ctx_no_key()) is False


def test_healthcheck_does_not_make_network_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def _fake_urlopen(*args, **kwargs):
        calls.append("network")
        raise AssertionError("should not call network during healthcheck")

    monkeypatch.setattr(
        "openminion.tools.weather.providers.weatherapi.provider.urllib_request.urlopen",
        _fake_urlopen,
    )
    provider = WeatherApiProvider(WeatherApiProviderConfig(api_key="k"))
    result = provider.healthcheck()
    assert result is True
    assert calls == []


def test_lookup_raises_dependency_missing_when_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = WeatherApiProvider()

    with pytest.raises(ToolRuntimeError) as exc_info:
        provider.lookup(
            query_args={"location": "London"},
            extension_args={},
            ctx=_ctx_no_key(),  # type: ignore[arg-type]
        )

    assert exc_info.value.code == "DEPENDENCY_MISSING"


def test_lookup_401_raises_upstream_error_auth_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openminion.tools.weather.providers.weatherapi.provider.urllib_request.urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(_http_error(401, b"Unauthorized")),
    )

    provider = WeatherApiProvider(WeatherApiProviderConfig(api_key="bad-key"))
    with pytest.raises(ToolRuntimeError) as exc_info:
        provider.lookup(
            query_args={"location": "London"},
            extension_args={},
            ctx=SimpleNamespace(),  # type: ignore[arg-type]
        )

    err = exc_info.value
    assert err.code == "UPSTREAM_ERROR"
    assert err.details.get("reason") == "auth_failed"


def test_lookup_403_raises_upstream_error_auth_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openminion.tools.weather.providers.weatherapi.provider.urllib_request.urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(_http_error(403, b"Forbidden")),
    )

    provider = WeatherApiProvider(WeatherApiProviderConfig(api_key="bad-key"))
    with pytest.raises(ToolRuntimeError) as exc_info:
        provider.lookup(
            query_args={"location": "London"},
            extension_args={},
            ctx=SimpleNamespace(),  # type: ignore[arg-type]
        )

    err = exc_info.value
    assert err.code == "UPSTREAM_ERROR"
    assert err.details.get("reason") == "auth_failed"


def test_lookup_429_raises_upstream_error_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "openminion.tools.weather.providers.weatherapi.provider.urllib_request.urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(_http_error(429, b"Rate limit")),
    )

    provider = WeatherApiProvider(WeatherApiProviderConfig(api_key="key"))
    with pytest.raises(ToolRuntimeError) as exc_info:
        provider.lookup(
            query_args={"location": "London"},
            extension_args={},
            ctx=SimpleNamespace(),  # type: ignore[arg-type]
        )

    err = exc_info.value
    assert err.code == "UPSTREAM_ERROR"
    assert err.details.get("reason") == "rate_limited"


def test_lookup_500_raises_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "openminion.tools.weather.providers.weatherapi.provider.urllib_request.urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(_http_error(500, b"Server error")),
    )

    provider = WeatherApiProvider(WeatherApiProviderConfig(api_key="key"))
    with pytest.raises(ToolRuntimeError) as exc_info:
        provider.lookup(
            query_args={"location": "London"},
            extension_args={},
            ctx=SimpleNamespace(),  # type: ignore[arg-type]
        )

    err = exc_info.value
    assert err.code == "UPSTREAM_ERROR"


def test_lookup_network_failure_raises_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from urllib import error as _uerror

    monkeypatch.setattr(
        "openminion.tools.weather.providers.weatherapi.provider.urllib_request.urlopen",
        lambda *a, **kw: (_ for _ in ()).throw(_uerror.URLError("network down")),
    )

    provider = WeatherApiProvider(WeatherApiProviderConfig(api_key="key"))
    with pytest.raises(ToolRuntimeError) as exc_info:
        provider.lookup(
            query_args={"location": "London"},
            extension_args={},
            ctx=SimpleNamespace(),  # type: ignore[arg-type]
        )

    assert exc_info.value.code == "UPSTREAM_ERROR"


def test_lookup_api_error_payload_raises_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error_body = json.dumps(
        {"error": {"code": 1006, "message": "No matching location found."}}
    ).encode("utf-8")

    class _FakeResp:
        def read(self):
            return error_body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "openminion.tools.weather.providers.weatherapi.provider.urllib_request.urlopen",
        lambda *a, **kw: _FakeResp(),
    )

    provider = WeatherApiProvider(WeatherApiProviderConfig(api_key="key"))
    with pytest.raises(ToolRuntimeError) as exc_info:
        provider.lookup(
            query_args={"location": "???"},
            extension_args={},
            ctx=SimpleNamespace(),  # type: ignore[arg-type]
        )

    assert exc_info.value.code == "UPSTREAM_ERROR"


def test_normalize_response_maps_to_shared_shape() -> None:
    result = _normalize_response(_SAMPLE_PAYLOAD, q="London")

    assert result["location"]["query"] == "London"
    assert result["location"]["resolved_name"] == "London"
    assert result["location"]["country"] == "United Kingdom"
    assert result["location"]["latitude"] == pytest.approx(51.52)
    assert result["location"]["longitude"] == pytest.approx(-0.11)
    assert result["observed_at"] == "2024-03-31 13:45"
    assert result["metrics"]["temperature_c"] == pytest.approx(13.5)
    assert result["metrics"]["humidity_pct"] == pytest.approx(72.0)
    assert result["metrics"]["wind_speed_kmh"] == pytest.approx(20.1)
    assert result["metrics"]["feels_like_c"] == pytest.approx(11.2)
    assert result["metrics"]["weather_code"] == pytest.approx(1003.0)
    assert result["summary"] == "Partly cloudy"
    assert result["source"]["provider"] == "weatherapi"
    assert isinstance(result["warnings"], list)
    assert result["verified"] is True


def test_lookup_success_returns_shared_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(_SAMPLE_PAYLOAD).encode("utf-8")

    class _FakeResp:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "openminion.tools.weather.providers.weatherapi.provider.urllib_request.urlopen",
        lambda *a, **kw: _FakeResp(),
    )

    provider = WeatherApiProvider(WeatherApiProviderConfig(api_key="key"))
    result = provider.lookup(
        query_args={"location": "London"},
        extension_args={},
        ctx=SimpleNamespace(),  # type: ignore[arg-type]
    )

    assert "location" in result
    assert "observed_at" in result
    assert "metrics" in result
    assert "source" in result
    assert "verified" in result
    assert "warnings" in result
    assert result["source"]["provider"] == "weatherapi"


def test_lookup_with_lat_lon_builds_correct_q(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_urls: list[str] = []
    body = json.dumps(_SAMPLE_PAYLOAD).encode("utf-8")

    class _FakeResp:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        captured_urls.append(req.full_url)
        return _FakeResp()

    monkeypatch.setattr(
        "openminion.tools.weather.providers.weatherapi.provider.urllib_request.urlopen",
        _fake_urlopen,
    )

    provider = WeatherApiProvider(WeatherApiProviderConfig(api_key="key"))
    provider.lookup(
        query_args={"latitude": 51.52, "longitude": -0.11},
        extension_args={},
        ctx=SimpleNamespace(),  # type: ignore[arg-type]
    )

    assert len(captured_urls) == 1
    assert "q=51.52%2C-0.11" in captured_urls[0] or "q=51.52,-0.11" in captured_urls[0]


def test_lookup_sends_lang_parameter_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_urls: list[str] = []
    body = json.dumps(_SAMPLE_PAYLOAD).encode("utf-8")

    class _FakeResp:
        def read(self):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, timeout=None):
        captured_urls.append(req.full_url)
        return _FakeResp()

    monkeypatch.setattr(
        "openminion.tools.weather.providers.weatherapi.provider.urllib_request.urlopen",
        _fake_urlopen,
    )

    provider = WeatherApiProvider(WeatherApiProviderConfig(api_key="key"))
    provider.lookup(
        query_args={"location": "London", "language": "fr"},
        extension_args={},
        ctx=SimpleNamespace(),  # type: ignore[arg-type]
    )

    assert len(captured_urls) == 1
    assert "lang=fr" in captured_urls[0]


def test_lookup_raises_invalid_argument_when_no_location() -> None:
    provider = WeatherApiProvider(WeatherApiProviderConfig(api_key="key"))
    with pytest.raises(ToolRuntimeError) as exc_info:
        provider.lookup(
            query_args={},
            extension_args={},
            ctx=SimpleNamespace(),  # type: ignore[arg-type]
        )
    assert exc_info.value.code == "INVALID_ARGUMENT"
