"""Location tool runtime helpers."""

import ipaddress
import socket
import urllib.parse
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from openminion.modules.tool.family.events import emit_family_event

from .constants import (
    LOCATION_PRIVACY_CITY,
    LOCATION_PRIVACY_LEVELS,
    LOCATION_PRIVACY_NONE,
    LOCATION_PRIVACY_REGION,
    LOCATION_SOURCE_IDENTITY_DEFAULT,
    LOCATION_SOURCE_IP_GEO,
    LOCATION_SOURCE_NONE,
    LOCATION_SOURCE_SESSION_OVERRIDE,
    LOCATION_SOURCE_VALUES,
)

LOCATION_TOOL_SOURCE = "location_module"
_NULLISH_LOCATION_TOKENS = frozenset({"none", "null", "nil", "undefined"})
ForbiddenIpCheck = Callable[
    [ipaddress.IPv4Address | ipaddress.IPv6Address],
    bool,
]


class NetworkPolicyError(Exception):
    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = str(code or "NETWORK_DENIED")
        self.message = str(message)
        self.details = dict(details or {})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def error_payload(
    code: str,
    message: str,
    *,
    method: str,
    source: str = LOCATION_SOURCE_NONE,
    warnings: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_source = (
        source if source in LOCATION_SOURCE_VALUES else LOCATION_SOURCE_NONE
    )
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
            "location_source": LOCATION_SOURCE_IDENTITY_DEFAULT,
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


def normalize_host_allowlist(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        token = raw.strip().lower()
        return (token,) if token else ()
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for item in raw:
        token = str(item or "").strip().lower()
        if token:
            out.append(token)
    return tuple(out)


def host_allowed(host: str, allowlist: tuple[str, ...]) -> bool:
    normalized = str(host or "").strip().lower()
    if not normalized:
        return False
    if not allowlist:
        return True
    for token in allowlist:
        if normalized == token or normalized.endswith(f".{token}"):
            return True
    return False


def validate_ip_lookup_url(
    url: str,
    *,
    cfg: Mapping[str, Any],
    is_forbidden_ip: ForbiddenIpCheck,
) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        raise NetworkPolicyError(
            "NETWORK_DENIED",
            "ip_lookup_url must be absolute",
            {"url": url},
        )

    allow_http = policy_flag(cfg, "allow_http", default=False)
    default_schemes = ["https", "http"] if allow_http else ["https"]
    scheme_allowlist = normalize_host_allowlist(
        cfg.get("scheme_allowlist", default_schemes)
    )
    if parsed.scheme.lower() not in set(scheme_allowlist):
        raise NetworkPolicyError(
            "NETWORK_DENIED",
            "ip_lookup_url scheme is not allowed",
            {"url": url, "scheme": parsed.scheme.lower()},
        )

    host = str(parsed.hostname or "").strip().lower()
    if not host:
        raise NetworkPolicyError(
            "NETWORK_DENIED",
            "ip_lookup_url host is missing",
            {"url": url},
        )

    host_allowlist = normalize_host_allowlist(cfg.get("allowed_hosts", ["ipapi.co"]))
    if not host_allowed(host, host_allowlist):
        raise NetworkPolicyError(
            "NETWORK_DENIED",
            "ip_lookup_url host is not allowed",
            {"host": host},
        )
    if policy_flag(cfg, "allow_private_hosts", default=False):
        return parsed.geturl()
    if host in {"localhost", "ip6-localhost", "0.0.0.0"}:
        raise NetworkPolicyError(
            "NETWORK_DENIED",
            "ip_lookup_url host is blocked by SSRF policy",
            {"host": host},
        )
    try:
        direct_ip = ipaddress.ip_address(host)
    except ValueError:
        direct_ip = None
    if direct_ip is not None and bool(is_forbidden_ip(direct_ip)):
        raise NetworkPolicyError(
            "NETWORK_DENIED",
            "ip_lookup_url host is blocked by SSRF policy",
            {"host": host},
        )
    try:
        resolved = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return parsed.geturl()
    for row in resolved:
        sockaddr = row[4]
        if not isinstance(sockaddr, tuple) or not sockaddr:
            continue
        candidate = str(sockaddr[0] or "").strip()
        if not candidate:
            continue
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if bool(is_forbidden_ip(ip)):
            raise NetworkPolicyError(
                "NETWORK_DENIED",
                "ip_lookup_url host resolves to private/loopback address",
                {"host": host, "resolved_ip": str(ip)},
            )
    return parsed.geturl()


def policy_flag(cfg: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = cfg.get(key, default)
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def confidence_for_source(source: str) -> str:
    if source in {LOCATION_SOURCE_SESSION_OVERRIDE, LOCATION_SOURCE_IDENTITY_DEFAULT}:
        return "high"
    if source == LOCATION_SOURCE_IP_GEO:
        return "low"
    return "low"


def apply_privacy(record: dict[str, Any], *, max_privacy: str) -> dict[str, Any]:
    normalized = dict(record)
    privacy = (
        str(max_privacy or LOCATION_PRIVACY_CITY).strip().lower()
        or LOCATION_PRIVACY_CITY
    )
    if privacy not in LOCATION_PRIVACY_LEVELS:
        privacy = LOCATION_PRIVACY_CITY
    if privacy == LOCATION_PRIVACY_NONE:
        normalized["city"] = None
        normalized["region"] = None
        normalized["country"] = None
        normalized["timezone"] = None
        normalized["lat"] = None
        normalized["lon"] = None
    elif privacy == LOCATION_PRIVACY_REGION:
        normalized["city"] = None
        normalized["lat"] = None
        normalized["lon"] = None
    elif privacy == LOCATION_PRIVACY_CITY:
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
        str(args.get("privacy_level", LOCATION_PRIVACY_CITY) or LOCATION_PRIVACY_CITY)
        .strip()
        .lower()
        or LOCATION_PRIVACY_CITY
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
                source=LOCATION_SOURCE_NONE,
            ),
        )
    if privacy_level not in LOCATION_PRIVACY_LEVELS:
        privacy_level = LOCATION_PRIVACY_CITY
    return city, region, country, timezone_name, privacy_level, None
