import copy
import json
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from openminion.base.version import OPENMINION_VERSION
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.context import RuntimeContext

from .constants import (
    WEATHER_OPENMETEO_BACKOFF_SECONDS,
    WEATHER_OPENMETEO_CANONICAL_TOOL,
    WEATHER_OPENMETEO_CONFIG_ENV,
    WEATHER_OPENMETEO_DEFAULT_USER_AGENT,
    WEATHER_OPENMETEO_FORECAST_ENDPOINT,
    WEATHER_OPENMETEO_GEOCODING_ENDPOINT,
    WEATHER_OPENMETEO_RAW_BODY_DETAIL_LIMIT,
    WEATHER_OPENMETEO_RETRYABLE_STATUS_CODES,
    WEATHER_OPENMETEO_SECONDARY_GEOCODING_ENDPOINT,
    WEATHER_OPENMETEO_SECONDARY_USER_AGENT,
)
from .runtime import as_float as _as_float
from .runtime import build_payload as _build_payload
from .runtime import deep_merge as _deep_merge
from .runtime import emit_event as _emit_event
from .runtime import fallback_sample as _fallback_sample
from .runtime import maybe_write_json_artifact as _maybe_write_json_artifact
from .runtime import normalize_location_text as _normalize_location_text
from .runtime import normalize_query_key as _normalize_query_key
from .runtime import sanitize_request as _sanitize_request
from .runtime import truncate_text as _truncate
from .runtime import verify_weather_result as _verify_weather_result
from .schemas import WeatherOpenMeteoArgs
from .schemas import WeatherOpenMeteoConfig

TOOL_DESCRIPTOR: Dict[str, Any] = {
    "name": WEATHER_OPENMETEO_CANONICAL_TOOL,
    "title": "Open-Meteo Current Weather",
    "description": "Resolve a location query via Open-Meteo geocoding and fetch current conditions.",
    "version": OPENMINION_VERSION,
    "capabilities": ["read_only", "network"],
    "risk_spec": {
        "risk_level": "low",
        "side_effects": "network_call",
        "default_policy": "allow",
    },
}


@dataclass
class _CacheEntry:
    expires_at: float
    payload: Dict[str, Any]


_CACHE: dict[str, _CacheEntry] = {}
_CACHE_LOCK = threading.Lock()


def _load_env_config(ctx: RuntimeContext) -> Dict[str, Any]:
    raw = str(ctx.env.get(WEATHER_OPENMETEO_CONFIG_ENV, "")).strip()
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"{WEATHER_OPENMETEO_CONFIG_ENV} must be valid JSON",
            {"error": str(exc)},
        ) from exc
    if not isinstance(decoded, dict):
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"{WEATHER_OPENMETEO_CONFIG_ENV} must be a JSON object",
        )
    return decoded


def _resolve_config(ctx: RuntimeContext) -> WeatherOpenMeteoConfig:
    policy_tools = (
        ctx.policy.raw.get("tools", {}) if isinstance(ctx.policy.raw, dict) else {}
    )
    from_policy: Dict[str, Any] = {}
    if isinstance(policy_tools, dict):
        candidate = policy_tools.get("weather_openmeteo")
        if isinstance(candidate, dict):
            from_policy = candidate

    config_payload = _deep_merge(from_policy, _load_env_config(ctx))
    return WeatherOpenMeteoConfig.model_validate(config_payload)


def resolve_openmeteo_config(ctx: RuntimeContext) -> WeatherOpenMeteoConfig:
    """Resolve Open-Meteo config for sibling tools that reuse this provider."""

    return _resolve_config(ctx)


def _resolve_location_argument(arguments: Mapping[str, Any]) -> str:
    for key in ("location", "city", "query", "place"):
        value = _normalize_location_text(arguments.get(key))
        if value:
            return value
    return ""


def _resolve_location_from_location_tool(ctx: RuntimeContext) -> str:
    try:
        from openminion.tools.location import plugin as location_plugin
    except Exception:
        return ""
    try:
        payload = location_plugin._h_get({}, ctx)
    except Exception:
        return ""
    if not isinstance(payload, Mapping) or not bool(payload.get("ok", False)):
        return ""
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return ""
    city = _normalize_location_text(data.get("city"))
    region = _normalize_location_text(data.get("region"))
    country = _normalize_location_text(data.get("country"))
    if city:
        return city
    if region and country:
        return f"{region}, {country}"
    if country:
        return country
    if region:
        return region
    return ""


def _resolve_coordinates_argument(
    arguments: Mapping[str, Any],
) -> tuple[float, float] | None:
    candidates = [
        ("latitude", "longitude"),
        ("lat", "lon"),
        ("latitude", "lon"),
        ("lat", "longitude"),
    ]
    for lat_key, lon_key in candidates:
        lat_raw = arguments.get(lat_key)
        lon_raw = arguments.get(lon_key)
        if lat_raw is None or lon_raw is None:
            continue
        try:
            return (float(lat_raw), float(lon_raw))
        except (TypeError, ValueError):
            continue
    return None


def _cache_get(config: WeatherOpenMeteoConfig, key: str) -> Optional[Dict[str, Any]]:
    if not config.caching.enabled:
        return None
    now = time.time()
    with _CACHE_LOCK:
        row = _CACHE.get(key)
        if row is None:
            return None
        if row.expires_at <= now:
            _CACHE.pop(key, None)
            return None
        return copy.deepcopy(row.payload)


def _cache_set(
    config: WeatherOpenMeteoConfig, key: str, payload: Dict[str, Any]
) -> None:
    if not config.caching.enabled:
        return
    ttl = max(1, int(config.caching.ttl_seconds))
    with _CACHE_LOCK:
        _CACHE[key] = _CacheEntry(
            expires_at=time.time() + ttl, payload=copy.deepcopy(payload)
        )


def _sleep_before_retry(*, attempt: int) -> None:
    idx = min(attempt, len(WEATHER_OPENMETEO_BACKOFF_SECONDS) - 1)
    delay = WEATHER_OPENMETEO_BACKOFF_SECONDS[idx] + random.uniform(0.0, 0.15)
    time.sleep(delay)


def _request_json(
    *,
    base_url: str,
    params: Mapping[str, str],
    timeout_s: float,
    retries: int,
    service_name: str = "Open-Meteo",
    user_agent: str = WEATHER_OPENMETEO_DEFAULT_USER_AGENT,
    require_mapping: bool = True,
) -> tuple[Any, str]:
    request_url = f"{base_url}?{urllib_parse.urlencode(dict(params))}"

    for attempt in range(max(0, int(retries)) + 1):
        request = urllib_request.Request(
            request_url,
            method="GET",
            headers={
                "Accept": "application/json",
                "User-Agent": user_agent,
            },
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout_s) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            if _retry_openmeteo_http_error(
                exc,
                attempt=attempt,
                retries=retries,
                request_url=request_url,
                service_name=service_name,
            ):
                continue
        except TimeoutError as exc:
            if attempt < retries:
                _sleep_before_retry(attempt=attempt)
                continue
            raise ToolRuntimeError(
                "TIMEOUT",
                f"{service_name} request timed out after {timeout_s} second(s)",
                {"timeout_s": timeout_s, "url": request_url},
            ) from exc
        except urllib_error.URLError as exc:
            if _retry_openmeteo_url_error(
                exc,
                attempt=attempt,
                retries=retries,
                timeout_s=timeout_s,
                request_url=request_url,
                service_name=service_name,
            ):
                continue

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ToolRuntimeError(
                "INVALID_RESPONSE",
                f"{service_name} returned invalid JSON",
                {
                    "url": request_url,
                    "raw_body": body[:WEATHER_OPENMETEO_RAW_BODY_DETAIL_LIMIT],
                },
            ) from exc
        if require_mapping and not isinstance(payload, dict):
            raise ToolRuntimeError(
                "INVALID_RESPONSE",
                f"{service_name} returned an unexpected payload shape",
                {
                    "url": request_url,
                    "raw_body": body[:WEATHER_OPENMETEO_RAW_BODY_DETAIL_LIMIT],
                },
            )
        return payload, request_url

    raise ToolRuntimeError(
        "UPSTREAM_ERROR", f"{service_name} request failed after retries"
    )


def _retry_openmeteo_http_error(
    exc: urllib_error.HTTPError,
    *,
    attempt: int,
    retries: int,
    request_url: str,
    service_name: str,
) -> bool:
    status_code = int(exc.code)
    body = ""
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    if status_code == 429:
        raise ToolRuntimeError(
            "RATE_LIMITED",
            f"{service_name} rate limit exceeded",
            {"status_code": status_code, "url": request_url, "body": _truncate(body, 500)},
        ) from exc
    if status_code in WEATHER_OPENMETEO_RETRYABLE_STATUS_CODES and attempt < retries:
        _sleep_before_retry(attempt=attempt)
        return True
    raise ToolRuntimeError(
        "UPSTREAM_ERROR",
        f"{service_name} request failed with status {status_code}",
        {"status_code": status_code, "url": request_url, "body": _truncate(body, 500)},
    ) from exc


def _retry_openmeteo_url_error(
    exc: urllib_error.URLError,
    *,
    attempt: int,
    retries: int,
    timeout_s: float,
    request_url: str,
    service_name: str,
) -> bool:
    reason = getattr(exc, "reason", exc)
    reason_text = str(reason)
    if attempt < retries:
        _sleep_before_retry(attempt=attempt)
        return True
    if isinstance(reason, TimeoutError) or "timed out" in reason_text.lower():
        raise ToolRuntimeError(
            "TIMEOUT",
            f"{service_name} request timed out after {timeout_s} second(s)",
            {"timeout_s": timeout_s, "url": request_url, "reason": reason_text},
        ) from exc
    raise ToolRuntimeError(
        "UPSTREAM_ERROR",
        f"{service_name} request failed",
        {"url": request_url, "reason": reason_text},
    ) from exc


def _geocode(
    query: str,
    *,
    config: WeatherOpenMeteoConfig,
    language: str,
    timeout_s: float,
) -> tuple[Dict[str, Any], str, Dict[str, Any]]:
    params: Dict[str, str] = {
        "name": query,
        "count": str(config.geocoding_count),
        "language": language,
        "format": "json",
    }
    if config.default_country_code:
        params["countryCode"] = config.default_country_code

    payload, request_url = _request_json(
        base_url=WEATHER_OPENMETEO_GEOCODING_ENDPOINT,
        params=params,
        timeout_s=timeout_s,
        retries=config.retries,
    )

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise ToolRuntimeError(
            "NOT_FOUND",
            f"Location not found: {query}",
            {"query": query, "verified": False},
        )

    first = results[0] if isinstance(results[0], dict) else {}
    try:
        latitude = _as_float(first.get("latitude"), field_name="latitude")
        longitude = _as_float(first.get("longitude"), field_name="longitude")
    except (TypeError, ValueError) as exc:
        raise ToolRuntimeError(
            "INVALID_RESPONSE", "Geocoding payload missing latitude/longitude"
        ) from exc

    return (
        {
            "resolved_name": str(first.get("name") or query).strip() or query,
            "country": str(first.get("country") or "").strip(),
            "latitude": latitude,
            "longitude": longitude,
        },
        request_url,
        payload,
    )


def geocode_openmeteo_location(
    query: str,
    *,
    config: WeatherOpenMeteoConfig,
    language: str,
    timeout_s: float,
) -> tuple[Dict[str, Any], str, Dict[str, Any]]:
    """Resolve a location through the primary Open-Meteo geocoder."""

    return _geocode(query, config=config, language=language, timeout_s=timeout_s)


def _secondary_geocode(
    query: str,
    *,
    config: WeatherOpenMeteoConfig,
    language: str,
    timeout_s: float,
) -> tuple[Dict[str, Any], str, list[Dict[str, Any]]]:
    params: Dict[str, str] = {
        "q": query,
        "format": "jsonv2",
        "limit": "1",
        "addressdetails": "1",
    }
    if language:
        params["accept-language"] = language
    if config.default_country_code:
        params["countrycodes"] = str(config.default_country_code).lower()

    payload, request_url = _request_json(
        base_url=WEATHER_OPENMETEO_SECONDARY_GEOCODING_ENDPOINT,
        params=params,
        timeout_s=timeout_s,
        retries=0,
        service_name="Nominatim",
        user_agent=WEATHER_OPENMETEO_SECONDARY_USER_AGENT,
        require_mapping=False,
    )
    if not isinstance(payload, list) or not payload:
        raise ToolRuntimeError(
            "NOT_FOUND",
            f"Location not found: {query}",
            {
                "query": query,
                "verified": False,
                "secondary_geocoder": "nominatim",
            },
        )

    first = payload[0] if isinstance(payload[0], dict) else {}
    try:
        latitude = _as_float(first.get("lat"), field_name="lat")
        longitude = _as_float(first.get("lon"), field_name="lon")
    except (TypeError, ValueError) as exc:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "Nominatim payload missing latitude/longitude",
        ) from exc

    address = first.get("address")
    country = (
        str(address.get("country") or "").strip()
        if isinstance(address, Mapping)
        else ""
    )
    resolved_name = (
        str(first.get("name") or first.get("display_name") or query).strip() or query
    )
    return (
        {
            "resolved_name": resolved_name,
            "country": country,
            "latitude": latitude,
            "longitude": longitude,
        },
        request_url,
        [entry for entry in payload if isinstance(entry, dict)],
    )


def secondary_geocode_openmeteo_location(
    query: str,
    *,
    config: WeatherOpenMeteoConfig,
    language: str,
    timeout_s: float,
) -> tuple[Dict[str, Any], str, list[Dict[str, Any]]]:
    """Resolve a location through the fallback Open-Meteo geocoder."""

    return _secondary_geocode(
        query, config=config, language=language, timeout_s=timeout_s
    )


def _forecast_current(
    *,
    latitude: float,
    longitude: float,
    config: WeatherOpenMeteoConfig,
    timeout_s: float,
) -> tuple[Dict[str, Any], str, Dict[str, Any]]:
    params: Dict[str, str] = {
        "latitude": str(latitude),
        "longitude": str(longitude),
        "current": ",".join(config.current_fields),
        "timezone": config.timezone,
    }
    if config.units.temperature == "fahrenheit":
        params["temperature_unit"] = "fahrenheit"
    if config.units.wind_speed == "mph":
        params["wind_speed_unit"] = "mph"

    payload, request_url = _request_json(
        base_url=WEATHER_OPENMETEO_FORECAST_ENDPOINT,
        params=params,
        timeout_s=timeout_s,
        retries=config.retries,
    )
    current = payload.get("current")
    if not isinstance(current, dict):
        raise ToolRuntimeError(
            "INVALID_RESPONSE", "Forecast payload missing current fields"
        )

    return current, request_url, payload


def forecast_openmeteo_current(
    *,
    latitude: float,
    longitude: float,
    config: WeatherOpenMeteoConfig,
    timeout_s: float,
) -> tuple[Dict[str, Any], str, Dict[str, Any]]:
    """Fetch current Open-Meteo conditions for sibling provider reuse."""

    return _forecast_current(
        latitude=latitude,
        longitude=longitude,
        config=config,
        timeout_s=timeout_s,
    )


def _normalize_metrics(
    current: Mapping[str, Any], config: WeatherOpenMeteoConfig
) -> tuple[Dict[str, float], str]:
    raw_temperature = _as_float(
        current.get("temperature_2m"), field_name="temperature_2m"
    )
    raw_humidity = _as_float(
        current.get("relative_humidity_2m"), field_name="relative_humidity_2m"
    )
    raw_wind_speed = _as_float(
        current.get("wind_speed_10m"), field_name="wind_speed_10m"
    )
    raw_weather_code = _as_float(current.get("weather_code"), field_name="weather_code")

    temperature_c = raw_temperature
    if config.units.temperature == "fahrenheit":
        temperature_c = (raw_temperature - 32.0) / 1.8

    wind_speed_kmh = raw_wind_speed
    if config.units.wind_speed == "mph":
        wind_speed_kmh = raw_wind_speed * 1.609344

    observed_at = str(current.get("time", "")).strip()
    if not observed_at:
        raise ToolRuntimeError(
            "INVALID_RESPONSE", "Forecast payload missing current.time"
        )

    return (
        {
            "temperature_c": round(temperature_c, 2),
            "humidity_pct": round(raw_humidity, 2),
            "wind_speed_kmh": round(wind_speed_kmh, 2),
            "weather_code": round(raw_weather_code, 2),
        },
        observed_at,
    )


def _should_use_fallback(*, config: WeatherOpenMeteoConfig, error_code: str) -> bool:
    if not config.fallback.enabled:
        return False
    if config.fallback.mode == "disabled":
        return False
    return error_code in {
        "TIMEOUT",
        "RATE_LIMITED",
        "UPSTREAM_ERROR",
        "INVALID_RESPONSE",
    }


def _run_weather_lookup(args: Dict[str, Any], ctx: RuntimeContext) -> Dict[str, Any]:
    validated = WeatherOpenMeteoArgs.model_validate(args)
    normalized_args = validated.model_dump(exclude_none=True)
    coordinates = _resolve_coordinates_argument(normalized_args)
    query = _resolve_location_argument(normalized_args)
    if not query and coordinates is None:
        query = _resolve_location_from_location_tool(ctx)
    if not query and coordinates is None:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "One of location/city/query/place or latitude+longitude is required",
        )

    config = _resolve_config(ctx)
    if not config.enabled:
        raise ToolRuntimeError(
            "POLICY_DENIED", "weather_openmeteo tool is disabled by configuration"
        )

    timeout_s = float(
        validated.timeout_s
        if validated.timeout_s is not None
        else config.timeout_seconds
    )
    language = (
        str(validated.language or config.default_language or "en").strip() or "en"
    )
    debug_enabled = bool(config.debug or validated.debug)

    warnings: list[str] = []

    query_key = query
    if not query_key and coordinates is not None:
        query_key = f"{coordinates[0]:.4f},{coordinates[1]:.4f}"
    cache_key = _normalize_query_key(query_key)
    sanitized_args = _sanitize_request(normalized_args)

    started = time.monotonic()
    _emit_event(
        ctx,
        event_name="tool.requested",
        payload={
            "tool": WEATHER_OPENMETEO_CANONICAL_TOOL,
            "request": sanitized_args,
            "query": query_key,
        },
    )
    _emit_event(
        ctx,
        event_name="tool.started",
        payload={
            "tool": WEATHER_OPENMETEO_CANONICAL_TOOL,
            "query": query_key,
        },
    )

    cached = _cache_get(config, cache_key)
    if cached is not None:
        duration_ms = int((time.monotonic() - started) * 1000)
        _emit_event(
            ctx,
            event_name="tool.completed",
            payload={
                "tool": WEATHER_OPENMETEO_CANONICAL_TOOL,
                "verified": bool(cached.get("verified", False)),
                "source": cached.get("source", {}),
                "cache_hit": True,
                "timings": {"duration_ms": duration_ms},
            },
        )
        return cached

    geocode_payload: Optional[Dict[str, Any]] = None
    geocoding_provider = "open-meteo"
    forecast_payload: Optional[Dict[str, Any]] = None

    try:
        if coordinates is not None:
            lat, lon = coordinates
            geocode_url = f"coordinates:{lat},{lon}"
            geocode_payload = {
                "provided_coordinates": {"latitude": lat, "longitude": lon}
            }
            location = {
                "resolved_name": query_key,
                "country": "",
                "latitude": lat,
                "longitude": lon,
            }
        else:
            try:
                location, geocode_url, geocode_payload = _geocode(
                    query_key,
                    config=config,
                    language=language,
                    timeout_s=timeout_s,
                )
            except ToolRuntimeError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                try:
                    location, geocode_url, secondary_payload = _secondary_geocode(
                        query_key,
                        config=config,
                        language=language,
                        timeout_s=timeout_s,
                    )
                except ToolRuntimeError as fallback_exc:
                    exc.details["secondary_geocoder"] = "nominatim"
                    exc.details["secondary_error_code"] = fallback_exc.code
                    if fallback_exc.code == "NOT_FOUND":
                        exc.details["user_hint"] = (
                            f"Try a fuller place name than '{query_key}'."
                        )
                    else:
                        exc.details["secondary_error_message"] = fallback_exc.message
                    raise exc
                geocoding_provider = "nominatim"
                geocode_payload = {"results": secondary_payload}
                warnings.append("geocode_fallback_used:nominatim")
        current, forecast_url, forecast_payload = _forecast_current(
            latitude=_as_float(location.get("latitude"), field_name="latitude"),
            longitude=_as_float(location.get("longitude"), field_name="longitude"),
            config=config,
            timeout_s=timeout_s,
        )
        metrics, observed_at = _normalize_metrics(current, config)

        payload = _build_payload(
            query=query_key,
            location=location,
            metrics=metrics,
            observed_at=observed_at,
            endpoints=[geocode_url, forecast_url],
            warnings=warnings,
            fallback_used=False,
            geocoding_provider=geocoding_provider,
        )

        debug_artifacts: list[str] = []
        if debug_enabled:
            geocode_ref = _maybe_write_json_artifact(
                ctx=ctx,
                query=query_key,
                stage="geocode",
                payload=geocode_payload,
            )
            if geocode_ref:
                debug_artifacts.append(geocode_ref)
            forecast_ref = _maybe_write_json_artifact(
                ctx=ctx, query=query_key, stage="forecast", payload=forecast_payload
            )
            if forecast_ref:
                debug_artifacts.append(forecast_ref)
        if debug_artifacts:
            payload["debug_artifacts"] = debug_artifacts

        _cache_set(config, cache_key, payload)

        duration_ms = int((time.monotonic() - started) * 1000)
        _emit_event(
            ctx,
            event_name="tool.completed",
            payload={
                "tool": WEATHER_OPENMETEO_CANONICAL_TOOL,
                "verified": bool(payload.get("verified", False)),
                "source": payload.get("source", {}),
                "timings": {"duration_ms": duration_ms},
            },
        )
        return payload
    except ToolRuntimeError as exc:
        if _should_use_fallback(config=config, error_code=exc.code):
            fallback = _fallback_sample(query_key)
            duration_ms = int((time.monotonic() - started) * 1000)
            _emit_event(
                ctx,
                event_name="tool.completed",
                payload={
                    "tool": WEATHER_OPENMETEO_CANONICAL_TOOL,
                    "verified": bool(fallback.get("verified", False)),
                    "source": fallback.get("source", {}),
                    "timings": {"duration_ms": duration_ms},
                    "fallback": True,
                },
            )
            return fallback

        if (
            debug_enabled
            and exc.code == "INVALID_RESPONSE"
            and "raw_body" in exc.details
        ):
            raw_ref = _maybe_write_json_artifact(
                ctx=ctx,
                query=query_key,
                stage="invalid-response",
                payload={
                    "raw_body": exc.details.get("raw_body"),
                    "details": exc.details,
                },
            )
            if raw_ref:
                exc.details["debug_artifact"] = raw_ref

        duration_ms = int((time.monotonic() - started) * 1000)
        _emit_event(
            ctx,
            event_name="tool.failed",
            payload={
                "tool": WEATHER_OPENMETEO_CANONICAL_TOOL,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
                "timings": {"duration_ms": duration_ms},
            },
        )
        raise
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        _emit_event(
            ctx,
            event_name="tool.failed",
            payload={
                "tool": WEATHER_OPENMETEO_CANONICAL_TOOL,
                "error": {
                    "code": "UPSTREAM_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                },
                "timings": {"duration_ms": duration_ms},
            },
        )
        raise ToolRuntimeError(
            "UPSTREAM_ERROR", f"{type(exc).__name__}: {exc}"
        ) from exc


def _h_weather_openmeteo_current(
    args: Dict[str, Any], ctx: RuntimeContext
) -> Dict[str, Any]:
    return _run_weather_lookup(args, ctx)


class OpenMeteoWeatherProvider:
    provider_id = "openmeteo"

    def lookup(
        self,
        *,
        query_args: Mapping[str, Any],
        extension_args: Mapping[str, Any],
        ctx: RuntimeContext,
    ) -> Mapping[str, Any]:
        del extension_args
        return _run_weather_lookup(dict(query_args), ctx)

    def healthcheck(self) -> bool:
        return True


def register(registry: ToolRegistry) -> None:
    from openminion.tools.weather import register_provider as register_weather_provider

    del registry
    register_weather_provider(OpenMeteoWeatherProvider())


__all__ = [
    "OpenMeteoWeatherProvider",
    "register",
    "forecast_openmeteo_current",
    "geocode_openmeteo_location",
    "resolve_openmeteo_config",
    "secondary_geocode_openmeteo_location",
    "_h_weather_openmeteo_current",
    "_normalize_query_key",
    "_resolve_location_argument",
    "_verify_weather_result",
    "TOOL_DESCRIPTOR",
]
