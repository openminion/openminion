"""Location tool runtime helpers."""

from datetime import datetime, timezone
from typing import Any
from collections.abc import Mapping

from openminion.modules.tool.family.events import emit_family_event

from .interfaces import LOCATION_SOURCE_VALUES

LOCATION_TOOL_SOURCE = "location_module"
_NULLISH_LOCATION_TOKENS = frozenset({"none", "null", "nil", "undefined"})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def error_payload(
    code: str,
    message: str,
    *,
    method: str,
    source: str = "none",
    warnings: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_source = source if source in LOCATION_SOURCE_VALUES else "none"
    warning_items = [str(item) for item in (warnings or []) if str(item).strip()]
    reason_code = str((details or {}).get("reason_code") or str(code).lower())
    return {
        "ok": False,
        "error": {
            "code": str(code),
            "message": str(message),
            "details": dict(details or {}),
        },
        "data": {
            "source": "openminion-tool-location",
            "method": method,
            "location_source": normalized_source,
            "reason_code": reason_code,
        },
        "warnings": warning_items,
    }


def emit_event(ctx: Any, *, event_name: str, payload: dict[str, Any]) -> None:
    emit_family_event(ctx, event=event_name, payload=payload)


def success_payload(
    *,
    method: str,
    source: str,
    privacy_level: str,
    confidence: str,
    city: str | None,
    region: str | None,
    country: str | None,
    timezone_name: str | None,
    lat: float | None,
    lon: float | None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    warning_items = [str(item) for item in (warnings or []) if str(item).strip()]
    observed_at = utc_now()
    summary_parts = [part for part in [city, region, country] if part]
    if summary_parts:
        summary = ", ".join(summary_parts)
        content = f"Location ({source}): {summary}"
    else:
        content = f"Location unavailable (source={source})"
    return {
        "ok": True,
        "content": content,
        "data": {
            "source": "openminion-tool-location",
            "method": method,
            "location_source": source,
            "privacy_level": privacy_level,
            "confidence": confidence,
            "city": city,
            "region": region,
            "country": country,
            "timezone": timezone_name,
            "lat": lat,
            "lon": lon,
            "observed_at": observed_at,
            "warnings": warning_items,
        },
        "warnings": warning_items,
        "verified": True,
        "source": LOCATION_TOOL_SOURCE,
    }


def success_set_default_payload(
    *,
    city: str,
    region: str | None,
    country: str | None,
    timezone_name: str | None,
    privacy_level: str,
    identity_version: int,
    identity_hash: str,
    agent_id: str,
) -> dict[str, Any]:
    summary_parts = [part for part in [city, region, country] if part]
    summary = ", ".join(summary_parts) if summary_parts else city
    return {
        "ok": True,
        "content": f"Updated default location to {summary}",
        "data": {
            "source": "openminion-tool-location",
            "method": "location.set_default",
            "location_source": "identity.default",
            "agent_id": agent_id,
            "location": {
                "city": city,
                "region": region,
                "country": country,
                "timezone": timezone_name,
                "privacy_level": privacy_level,
            },
            "identity_version": int(identity_version),
            "identity_hash": str(identity_hash),
        },
        "warnings": [],
        "verified": True,
        "source": LOCATION_TOOL_SOURCE,
    }


def normalize_location_record(payload: Mapping[str, Any]) -> dict[str, Any]:
    city = _clean_location_text(payload.get("city"))
    region = _clean_location_text(payload.get("region"))
    country = _clean_location_text(payload.get("country"))
    timezone_name = _clean_location_text(payload.get("timezone"))
    lat_value = payload.get("lat", payload.get("latitude"))
    lon_value = payload.get("lon", payload.get("longitude"))
    lat: float | None
    lon: float | None
    try:
        lat = float(lat_value) if lat_value not in (None, "") else None
    except (ValueError, TypeError):
        lat = None
    try:
        lon = float(lon_value) if lon_value not in (None, "") else None
    except (ValueError, TypeError):
        lon = None
    return {
        "city": city,
        "region": region,
        "country": country,
        "timezone": timezone_name,
        "lat": lat,
        "lon": lon,
    }


def _clean_location_text(value: Any) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    if token.lower() in _NULLISH_LOCATION_TOKENS:
        return None
    return token


def has_location_data(record: Mapping[str, Any]) -> bool:
    for key in ("city", "region", "country", "timezone", "lat", "lon"):
        if record.get(key) not in (None, ""):
            return True
    return False


def confidence_for_source(source: str) -> str:
    if source in {"session.override", "identity.default"}:
        return "high"
    if source == "ip.geo":
        return "low"
    return "low"


def apply_privacy(record: dict[str, Any], *, max_privacy: str) -> dict[str, Any]:
    normalized = dict(record)
    privacy = str(max_privacy or "city").strip().lower() or "city"
    if privacy not in {"none", "city", "region", "precise"}:
        privacy = "city"
    if privacy == "none":
        normalized["city"] = None
        normalized["region"] = None
        normalized["country"] = None
        normalized["timezone"] = None
        normalized["lat"] = None
        normalized["lon"] = None
    elif privacy == "region":
        normalized["city"] = None
        normalized["lat"] = None
        normalized["lon"] = None
    elif privacy == "city":
        normalized["lat"] = None
        normalized["lon"] = None
    return normalized


def location_set_default_args(
    args: Mapping[str, Any],
) -> tuple[str, str | None, str | None, str | None, str, dict[str, Any] | None]:
    city = str(args.get("city", "")).strip()
    region = str(args.get("region", "")).strip() or None
    country = str(args.get("country", "")).strip() or None
    timezone_name = str(args.get("timezone", "")).strip() or None
    privacy_level = (
        str(args.get("privacy_level", "city") or "city").strip().lower() or "city"
    )
    if not city:
        return (
            city,
            region,
            country,
            timezone_name,
            privacy_level,
            error_payload(
                "INVALID_ARGUMENT",
                "city is required",
                method="location.set_default",
                source="none",
            ),
        )
    if privacy_level not in {"none", "city", "region", "precise"}:
        privacy_level = "city"
    return city, region, country, timezone_name, privacy_level, None
