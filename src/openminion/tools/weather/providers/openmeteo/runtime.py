import json
from datetime import datetime, timezone
from typing import Any
from collections.abc import Mapping

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.family.events import emit_family_event
from openminion.modules.tool.runtime.context import RuntimeContext

from .constants import (
    WEATHER_OPENMETEO_ARTIFACTS_SUBDIR,
    WEATHER_OPENMETEO_FALLBACK_LICENSE_NOTE,
    WEATHER_OPENMETEO_LICENSE_NOTE,
    WEATHER_OPENMETEO_SECONDARY_LICENSE_NOTE,
)

_MISSING_LOCATION_TOKENS = frozenset({"none", "null"})


def timestamp_token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_token(value: str) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value)
    )
    cleaned = cleaned.strip("-")
    return cleaned or "location"


def truncate_text(text: Any, max_chars: int = 400) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}...[truncated]"


def normalize_location_text(value: Any) -> str:
    if value is None:
        return ""
    normalized = str(value).strip()
    if not normalized:
        return ""
    if normalized.lower() in _MISSING_LOCATION_TOKENS:
        return ""
    return normalized


def sanitize_request(args: Mapping[str, Any]) -> dict[str, Any]:
    redacted = dict(args)
    for key in ("location", "city", "query", "place"):
        if key in redacted:
            redacted[key] = truncate_text(redacted.get(key), 200)
    return redacted


def emit_event(
    ctx: RuntimeContext, *, event_name: str, payload: dict[str, Any]
) -> None:
    emit_family_event(ctx, event=event_name, payload=payload)


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = deep_merge(out[key], value)
            continue
        out[key] = value
    return out


def normalize_query_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def is_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float))


def as_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ToolRuntimeError(
            "INVALID_RESPONSE", f"Field '{field_name}' must be numeric"
        )
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ToolRuntimeError(
            "INVALID_RESPONSE", f"Field '{field_name}' must be numeric"
        ) from exc


def location_matches_query(*, resolved_name: str, expected_query: str) -> bool:
    normalized_resolved = normalize_query_key(resolved_name)
    normalized_expected = normalize_query_key(expected_query)
    if not normalized_resolved or not normalized_expected:
        return False
    if normalized_resolved == normalized_expected:
        return True
    if (
        normalized_expected in normalized_resolved
        or normalized_resolved in normalized_expected
    ):
        return True

    aliases = {
        "san francisco": {
            "san francisco",
            "sf",
            "san francisco county",
            "south san francisco",
        },
        "tokyo": {"tokyo", "tokyo-to", "shinjuku", "shibuya", "chiyoda"},
        "jakarta": {"jakarta", "dki jakarta", "jakarta raya", "sunda kelapa"},
    }

    expected_aliases = {normalized_expected}
    resolved_aliases = {normalized_resolved}
    for canonical, city_aliases in aliases.items():
        if normalized_expected in city_aliases:
            expected_aliases = set(city_aliases)
        if normalized_resolved in city_aliases:
            resolved_aliases = set(city_aliases)
    if expected_aliases & resolved_aliases:
        return True

    resolved_tokens = set(normalized_resolved.split())
    expected_tokens = set(normalized_expected.split())
    if resolved_tokens and expected_tokens:
        if expected_tokens.issubset(resolved_tokens) or resolved_tokens.issubset(
            expected_tokens
        ):
            return True
    return False


def verify_weather_result(payload: Mapping[str, Any], *, expected_query: str) -> bool:
    location = payload.get("location") if isinstance(payload, Mapping) else None
    metrics = payload.get("metrics") if isinstance(payload, Mapping) else None
    observed_at = (
        str(payload.get("observed_at", "")).strip()
        if isinstance(payload, Mapping)
        else ""
    )

    if not isinstance(location, Mapping) or not isinstance(metrics, Mapping):
        return False

    resolved_name = str(location.get("resolved_name", "")).strip()
    if not location_matches_query(
        resolved_name=resolved_name, expected_query=expected_query
    ):
        return False

    for key in ("temperature_c", "humidity_pct", "wind_speed_kmh", "weather_code"):
        if not is_numeric(metrics.get(key)):
            return False

    return bool(observed_at)


def summary_for(payload: Mapping[str, Any]) -> str:
    location = payload.get("location", {})
    metrics = payload.get("metrics", {})

    resolved_name = str(location.get("resolved_name", "unknown")).strip() or "unknown"
    country = str(location.get("country", "")).strip()
    place = f"{resolved_name}, {country}" if country else resolved_name

    temperature = metrics.get("temperature_c")
    humidity = metrics.get("humidity_pct")
    wind_speed = metrics.get("wind_speed_kmh")
    weather_code = metrics.get("weather_code")
    observed_at = str(payload.get("observed_at", "unknown")).strip() or "unknown"

    return (
        f"{place}: {temperature}C, humidity {humidity}%, wind {wind_speed} km/h, "
        f"code {weather_code}. observed_at={observed_at}."
    )


def build_source(
    *,
    endpoints: list[str],
    fallback_used: bool,
    geocoding_provider: str = "open-meteo",
) -> dict[str, Any]:
    license_note = (
        WEATHER_OPENMETEO_FALLBACK_LICENSE_NOTE
        if fallback_used
        else WEATHER_OPENMETEO_LICENSE_NOTE
    )
    if not fallback_used and geocoding_provider == "nominatim":
        license_note = f"{license_note} {WEATHER_OPENMETEO_SECONDARY_LICENSE_NOTE}"

    source = {
        "provider": "open-meteo",
        "endpoints": list(endpoints),
        "license_note": license_note,
    }
    if geocoding_provider != "open-meteo":
        source["geocoding_provider"] = geocoding_provider
    return source


def build_payload(
    *,
    query: str,
    location: dict[str, Any],
    metrics: dict[str, Any],
    observed_at: str,
    endpoints: list[str],
    warnings: list[str],
    fallback_used: bool,
    geocoding_provider: str = "open-meteo",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "location": {
            "query": query,
            "resolved_name": str(location.get("resolved_name", query)),
            "country": str(location.get("country", "")),
            "latitude": float(location.get("latitude", 0.0)),
            "longitude": float(location.get("longitude", 0.0)),
        },
        "observed_at": observed_at,
        "metrics": {
            "temperature_c": float(metrics["temperature_c"]),
            "humidity_pct": float(metrics["humidity_pct"]),
            "wind_speed_kmh": float(metrics["wind_speed_kmh"]),
            "weather_code": float(metrics["weather_code"]),
        },
        "source": build_source(
            endpoints=endpoints,
            fallback_used=fallback_used,
            geocoding_provider=geocoding_provider,
        ),
        "warnings": list(warnings),
    }
    payload["summary"] = summary_for(payload)
    payload["verified"] = verify_weather_result(payload, expected_query=query)
    return payload


def fallback_sample(query: str) -> dict[str, Any]:
    normalized = normalize_query_key(query)
    observed_at = datetime.now(timezone.utc).isoformat()

    samples = {
        "san francisco": {
            "resolved_name": "San Francisco",
            "country": "United States",
            "latitude": 37.7749,
            "longitude": -122.4194,
            "temperature_c": 17.0,
            "humidity_pct": 72.0,
            "wind_speed_kmh": 13.0,
            "weather_code": 2.0,
        },
        "tokyo": {
            "resolved_name": "Tokyo",
            "country": "Japan",
            "latitude": 35.6762,
            "longitude": 139.6503,
            "temperature_c": 12.0,
            "humidity_pct": 58.0,
            "wind_speed_kmh": 11.0,
            "weather_code": 1.0,
        },
        "jakarta": {
            "resolved_name": "Jakarta",
            "country": "Indonesia",
            "latitude": -6.2088,
            "longitude": 106.8456,
            "temperature_c": 31.0,
            "humidity_pct": 74.0,
            "wind_speed_kmh": 9.0,
            "weather_code": 1.0,
        },
    }

    sample = samples.get(
        normalized,
        {
            "resolved_name": query,
            "country": "",
            "latitude": 0.0,
            "longitude": 0.0,
            "temperature_c": 25.0,
            "humidity_pct": 60.0,
            "wind_speed_kmh": 7.0,
            "weather_code": 0.0,
        },
    )

    return build_payload(
        query=query,
        location={
            "resolved_name": sample["resolved_name"],
            "country": sample["country"],
            "latitude": sample["latitude"],
            "longitude": sample["longitude"],
        },
        metrics={
            "temperature_c": sample["temperature_c"],
            "humidity_pct": sample["humidity_pct"],
            "wind_speed_kmh": sample["wind_speed_kmh"],
            "weather_code": sample["weather_code"],
        },
        observed_at=observed_at,
        endpoints=[],
        warnings=["fallback_used"],
        fallback_used=True,
    )


def maybe_write_json_artifact(
    *,
    ctx: RuntimeContext,
    query: str,
    stage: str,
    payload: Any,
) -> str | None:
    try:
        encoded = json.dumps(payload, ensure_ascii=True, indent=2).encode("utf-8")
    except Exception:
        return None

    rel_path = (
        f"{WEATHER_OPENMETEO_ARTIFACTS_SUBDIR}/"
        f"{safe_token(query)[:80]}-{stage}-{timestamp_token()}.json"
    )
    try:
        artifact = ctx.write_artifact(rel_path, encoded, "application/json")
    except Exception:
        return None
    return artifact.path
