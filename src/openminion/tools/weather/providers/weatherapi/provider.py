import json
from collections.abc import Mapping
from typing import Any, NoReturn
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.runtime import RuntimeContext

from .config import (
    DEFAULT_WEATHERAPI_API_URL,
    DEFAULT_WEATHERAPI_TIMEOUT_SECONDS,
    WeatherApiProviderConfig,
    resolve_weatherapi_api_key,
    resolve_weatherapi_api_url,
    resolve_weatherapi_timeout_seconds,
)
from .constants import (
    WEATHERAPI_CURRENT_PATH,
    WEATHERAPI_DISPLAY_NAME,
    WEATHERAPI_PROVIDER_ID,
)

_MISSING_LOCATION_TOKENS = frozenset({"none", "null"})


def _current_url(base_url: str) -> str:
    normalized = str(base_url or DEFAULT_WEATHERAPI_API_URL).strip().rstrip("/")
    if normalized.endswith(WEATHERAPI_CURRENT_PATH):
        return normalized
    return f"{normalized}{WEATHERAPI_CURRENT_PATH}"


def _resolve_q(query_args: Mapping[str, Any]) -> str:
    for key in ("location", "city", "query", "place"):
        raw_value = query_args.get(key)
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if value.lower() in _MISSING_LOCATION_TOKENS:
            continue
        if value:
            return value
    lat = query_args.get("latitude")
    lon = query_args.get("longitude")
    if lat is not None and lon is not None:
        return f"{float(lat)},{float(lon)}"
    return ""


def _raise_weatherapi_http_error(exc: urllib_error.HTTPError) -> NoReturn:
    status = int(exc.code)
    body_text = ""
    try:
        body_text = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body_text = ""
    if status in {401, 403}:
        raise ToolRuntimeError(
            "UPSTREAM_ERROR",
            f"{WEATHERAPI_DISPLAY_NAME} authentication failed (HTTP {status})",
            {"reason": "auth_failed", "status_code": status, "body": body_text[:500]},
        ) from exc
    if status == 429:
        raise ToolRuntimeError(
            "UPSTREAM_ERROR",
            f"{WEATHERAPI_DISPLAY_NAME} rate limit exceeded (HTTP {status})",
            {"reason": "rate_limited", "status_code": status, "body": body_text[:500]},
        ) from exc
    raise ToolRuntimeError(
        "UPSTREAM_ERROR",
        f"{WEATHERAPI_DISPLAY_NAME} request failed with status {status}",
        {"status_code": status, "body": body_text[:500]},
    ) from exc


def _weatherapi_payload_from_body(raw_body: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise ToolRuntimeError(
            "UPSTREAM_ERROR",
            f"{WEATHERAPI_DISPLAY_NAME} returned invalid JSON",
            {"reason": "invalid_json", "body": raw_body[:500]},
        ) from exc
    if not isinstance(payload, dict):
        raise ToolRuntimeError(
            "UPSTREAM_ERROR",
            f"{WEATHERAPI_DISPLAY_NAME} returned an unexpected payload shape",
            {"reason": "unexpected_shape"},
        )
    return payload


def _raise_weatherapi_payload_error(payload: Mapping[str, Any]) -> None:
    if "error" not in payload:
        return
    err = payload["error"] or {}
    err_code = int(err.get("code", 0))
    err_msg = str(err.get("message", "unknown error"))
    if err_code in {1002, 2006, 2007, 2008, 2009}:
        raise ToolRuntimeError(
            "UPSTREAM_ERROR",
            f"{WEATHERAPI_DISPLAY_NAME} authentication error: {err_msg}",
            {"reason": "auth_failed", "api_error_code": err_code},
        )
    raise ToolRuntimeError(
        "UPSTREAM_ERROR",
        f"{WEATHERAPI_DISPLAY_NAME} error: {err_msg}",
        {"api_error_code": err_code},
    )


def _normalize_response(payload: dict[str, Any], *, q: str) -> dict[str, Any]:
    location_raw = payload.get("location") or {}
    current_raw = payload.get("current") or {}
    condition_raw = current_raw.get("condition") or {}

    resolved_name = str(location_raw.get("name", "") or "").strip()
    region = str(location_raw.get("region", "") or "").strip()
    country = str(location_raw.get("country", "") or "").strip()
    lat = float(location_raw.get("lat", 0.0) or 0.0)
    lon = float(location_raw.get("lon", 0.0) or 0.0)
    localtime = str(location_raw.get("localtime_epoch", "") or "").strip()
    last_updated = str(current_raw.get("last_updated", "") or "").strip()

    temp_c = current_raw.get("temp_c")
    humidity = current_raw.get("humidity")
    wind_kph = current_raw.get("wind_kph")
    feelslike_c = current_raw.get("feelslike_c")
    precip_mm = current_raw.get("precip_mm")
    cloud = current_raw.get("cloud")
    vis_km = current_raw.get("vis_km")
    uv = current_raw.get("uv")
    condition_text = str(condition_raw.get("text", "") or "").strip()
    condition_code = condition_raw.get("code")

    metrics: dict[str, Any] = {}
    if temp_c is not None:
        metrics["temperature_c"] = float(temp_c)
    if humidity is not None:
        metrics["humidity_pct"] = float(humidity)
    if wind_kph is not None:
        metrics["wind_speed_kmh"] = float(wind_kph)
    if feelslike_c is not None:
        metrics["feels_like_c"] = float(feelslike_c)
    if precip_mm is not None:
        metrics["precipitation_mm"] = float(precip_mm)
    if cloud is not None:
        metrics["cloud_pct"] = float(cloud)
    if vis_km is not None:
        metrics["visibility_km"] = float(vis_km)
    if uv is not None:
        metrics["uv_index"] = float(uv)
    if condition_code is not None:
        metrics["weather_code"] = float(condition_code)

    location: dict[str, Any] = {
        "query": q,
        "resolved_name": resolved_name or q,
        "country": country,
        "latitude": lat,
        "longitude": lon,
    }
    if region:
        location["region"] = region

    return {
        "location": location,
        "observed_at": last_updated or localtime,
        "metrics": metrics,
        "summary": condition_text or None,
        "source": {
            "provider": WEATHERAPI_PROVIDER_ID,
            "display_name": WEATHERAPI_DISPLAY_NAME,
        },
        "verified": bool(
            resolved_name
            and last_updated
            and temp_c is not None
            and humidity is not None
            and wind_kph is not None
        ),
        "warnings": [],
    }


class WeatherApiProvider:
    provider_id = WEATHERAPI_PROVIDER_ID
    display_name = WEATHERAPI_DISPLAY_NAME

    def __init__(self, config: WeatherApiProviderConfig | None = None) -> None:
        self.config = config or WeatherApiProviderConfig()

    def _api_key(self, ctx: Any | None = None) -> str:
        if self.config.api_key and self.config.api_key.strip():
            return self.config.api_key.strip()
        return resolve_weatherapi_api_key(ctx=ctx)

    def _api_url(self, ctx: Any | None = None) -> str:
        if self.config.api_url and self.config.api_url.strip():
            return _current_url(self.config.api_url)
        return _current_url(resolve_weatherapi_api_url(ctx=ctx))

    def _timeout_s(self, ctx: Any | None = None) -> float:
        if self.config.timeout_s > 0:
            return float(self.config.timeout_s)
        resolved = resolve_weatherapi_timeout_seconds(ctx=ctx)
        return resolved if resolved > 0 else float(DEFAULT_WEATHERAPI_TIMEOUT_SECONDS)

    def healthcheck(self, ctx: Any | None = None) -> bool:
        return bool(self._api_key(ctx=ctx))

    def lookup(
        self,
        *,
        query_args: Mapping[str, Any],
        extension_args: Mapping[str, Any],
        ctx: RuntimeContext,
    ) -> Mapping[str, Any]:
        del extension_args

        api_key = self._api_key(ctx=ctx)
        if not api_key:
            raise ToolRuntimeError(
                "DEPENDENCY_MISSING",
                "WEATHERAPI_API_KEY is required for the weatherapi provider",
            )

        q = _resolve_q(query_args)
        if not q:
            raise ToolRuntimeError(
                "INVALID_ARGUMENT",
                "A location (location, city, query, place, or lat+lon) is required",
            )

        params: dict[str, str] = {"key": api_key, "q": q}
        lang = str(query_args.get("language", "") or "").strip()
        if lang:
            params["lang"] = lang

        url = f"{self._api_url(ctx=ctx)}?{urllib_parse.urlencode(params)}"
        req = urllib_request.Request(
            url,
            headers={"Accept": "application/json"},
            method="GET",
        )

        raw_body = self._read_json_body(req, ctx=ctx)
        payload = _weatherapi_payload_from_body(raw_body)
        _raise_weatherapi_payload_error(payload)
        return _normalize_response(payload, q=q)

    def _read_json_body(self, req: urllib_request.Request, *, ctx: RuntimeContext) -> str:
        try:
            with urllib_request.urlopen(req, timeout=self._timeout_s(ctx=ctx)) as resp:
                body: bytes = resp.read()
                return body.decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            _raise_weatherapi_http_error(exc)
        except urllib_error.URLError as exc:
            raise ToolRuntimeError(
                "UPSTREAM_ERROR",
                f"{WEATHERAPI_DISPLAY_NAME} request failed",
                {"reason": str(getattr(exc, "reason", exc))},
            ) from exc



__all__ = [
    "WeatherApiProvider",
]
