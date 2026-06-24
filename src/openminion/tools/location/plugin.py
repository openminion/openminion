"""Location tool plugin."""

import json
import ipaddress
import socket
import time
import urllib.parse
import urllib.request
from typing import Any, Mapping

from openminion.modules.tool.runtime.network import (
    is_forbidden_ip as _is_forbidden_ip,
)
from openminion.modules.tool.runtime.environment import (
    agent_id_from_context as _agent_id_from_context,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime.context import RuntimeContext
from openminion.modules.tool.runtime.context import resolve_identity_repository

from .config import LOCATION_IP_FALLBACK_URL, LOCATION_IP_RETRY_BACKOFF_SECONDS
from .constants import (
    LOCATION_REASON_RECORD_NOT_FOUND,
    LOCATION_REASON_STORAGE_EXEC_ERROR,
    LOCATION_REASON_STORAGE_UNAVAILABLE,
    LOCATION_REASON_STORAGE_UNCONFIGURED,
    LOCATION_SCOPE_ORDER,
)
from .runtime import apply_privacy as _apply_privacy
from .runtime import confidence_for_source as _confidence_for_source
from .runtime import emit_event as _emit_event
from .runtime import error_payload as _error
from .runtime import has_location_data as _has_location_data
from .runtime import location_set_default_args as _location_set_default_args
from .runtime import normalize_location_record as _normalize_location_record
from .runtime import success_payload as _success
from .runtime import success_set_default_payload as _success_set_default
from .runtime import utc_now as _utc_now
from .schemas import LocationGetArgs, LocationGetIPArgs, LocationSetDefaultArgs

_IP_CACHE: dict[str, Any] = {"expires_at": 0.0, "record": None}


class _NetworkPolicyError(Exception):
    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = str(code or "NETWORK_DENIED")
        self.message = str(message)
        self.details = dict(details or {})


def _location_policy_config(ctx: Any) -> dict[str, Any]:
    if not isinstance(ctx, RuntimeContext):
        return {}
    raw = getattr(getattr(ctx, "policy", None), "raw", {})
    if not isinstance(raw, Mapping):
        return {}
    tools_cfg = raw.get("tools")
    if not isinstance(tools_cfg, Mapping):
        return {}
    location_cfg = tools_cfg.get("location")
    return dict(location_cfg) if isinstance(location_cfg, Mapping) else {}


def _policy_flag(cfg: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = cfg.get(key, default)
    if isinstance(value, bool):
        return value
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def _normalize_host_allowlist(raw: Any) -> tuple[str, ...]:
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


def _host_allowed(host: str, allowlist: tuple[str, ...]) -> bool:
    normalized = str(host or "").strip().lower()
    if not normalized:
        return False
    if not allowlist:
        return True
    for token in allowlist:
        if normalized == token or normalized.endswith(f".{token}"):
            return True
    return False


def _validate_ip_lookup_url(url: str, *, cfg: Mapping[str, Any]) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        raise _NetworkPolicyError(
            "NETWORK_DENIED",
            "ip_lookup_url must be absolute",
            {"url": url},
        )

    allow_http = _policy_flag(cfg, "allow_http", default=False)
    default_schemes = ["https", "http"] if allow_http else ["https"]
    scheme_allowlist = _normalize_host_allowlist(
        cfg.get("scheme_allowlist", default_schemes)
    )
    if parsed.scheme.lower() not in set(scheme_allowlist):
        raise _NetworkPolicyError(
            "NETWORK_DENIED",
            "ip_lookup_url scheme is not allowed",
            {"url": url, "scheme": parsed.scheme.lower()},
        )

    host = str(parsed.hostname or "").strip().lower()
    if not host:
        raise _NetworkPolicyError(
            "NETWORK_DENIED",
            "ip_lookup_url host is missing",
            {"url": url},
        )

    host_allowlist = _normalize_host_allowlist(cfg.get("allowed_hosts", ["ipapi.co"]))
    if not _host_allowed(host, host_allowlist):
        raise _NetworkPolicyError(
            "NETWORK_DENIED",
            "ip_lookup_url host is not allowed",
            {"host": host},
        )

    if _policy_flag(cfg, "allow_private_hosts", default=False):
        return parsed.geturl()

    if host in {"localhost", "ip6-localhost", "0.0.0.0"}:
        raise _NetworkPolicyError(
            "NETWORK_DENIED",
            "ip_lookup_url host is blocked by SSRF policy",
            {"host": host},
        )
    try:
        direct_ip = ipaddress.ip_address(host)
    except ValueError:
        direct_ip = None
    if direct_ip is not None and _is_forbidden_ip(direct_ip):
        raise _NetworkPolicyError(
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
        if _is_forbidden_ip(ip):
            raise _NetworkPolicyError(
                "NETWORK_DENIED",
                "ip_lookup_url host resolves to private/loopback address",
                {"host": host, "resolved_ip": str(ip)},
            )
    return parsed.geturl()


def _scope_rank(value: str) -> int:
    return LOCATION_SCOPE_ORDER.get(str(value or "").strip().upper(), -1)


def _require_scope(
    ctx: RuntimeContext, *, required: str, method: str
) -> dict[str, Any] | None:
    required_scope = str(required or "READ_ONLY").strip().upper() or "READ_ONLY"
    current_scope = str(getattr(ctx, "scope", "")).strip().upper()
    if _scope_rank(current_scope) >= _scope_rank(required_scope):
        return None
    return _error(
        "POLICY_DENIED",
        f"{method} requires scope {required_scope}",
        method=method,
        source="none",
        details={
            "required_scope": required_scope,
            "current_scope": current_scope,
        },
    )


def _identity_location_from_meta(meta: Mapping[str, Any]) -> dict[str, Any] | None:
    nested_keys = ("home_location", "location_default", "default_location", "location")
    for key in nested_keys:
        raw_value = meta.get(key)
        if isinstance(raw_value, Mapping):
            record = _normalize_location_record(raw_value)
            if _has_location_data(record):
                return record

    flattened = {
        "city": meta.get("location_city", meta.get("city")),
        "region": meta.get("location_region", meta.get("region")),
        "country": meta.get("location_country", meta.get("country")),
        "timezone": meta.get("location_timezone", meta.get("timezone")),
        "lat": meta.get("location_lat", meta.get("lat")),
        "lon": meta.get("location_lon", meta.get("lon")),
    }
    record = _normalize_location_record(flattened)
    if _has_location_data(record):
        return record
    return None


def _location_from_identity_profile(ctx: RuntimeContext) -> dict[str, Any] | None:
    repository = resolve_identity_repository(ctx)
    if repository is None:
        return None
    agent_id = _agent_id_from_context(ctx)
    try:
        profile = repository.get_profile(agent_id)
    except Exception:
        return None
    if profile is None:
        return None
    meta = getattr(profile, "meta", None)
    if not isinstance(meta, Mapping):
        return None
    record = _identity_location_from_meta(meta)
    if record is not None:
        return record
    return None


def _identity_dependency_error(ctx: RuntimeContext) -> ToolRuntimeError:
    identity_path = getattr(ctx.repositories, "identity_path", None)
    if identity_path is None:
        return ToolRuntimeError(
            "DEPENDENCY_MISSING",
            "Identity storage is not configured",
            {"reason_code": LOCATION_REASON_STORAGE_UNCONFIGURED},
        )
    return ToolRuntimeError(
        "DEPENDENCY_MISSING",
        "Identity storage is unavailable",
        {
            "reason_code": LOCATION_REASON_STORAGE_UNAVAILABLE,
            "identity_path": str(identity_path),
        },
    )


def _ensure_identity_profile(repository: Any, *, agent_id: str) -> Any:
    try:
        profile = repository.get_profile(agent_id)
    except Exception as exc:
        raise ToolRuntimeError(
            "EXEC_ERROR",
            "Failed to load identity profile",
            {"reason_code": LOCATION_REASON_STORAGE_EXEC_ERROR, "reason": str(exc)},
        ) from exc
    if profile is not None:
        return profile

    from openminion.modules.identity.models import (
        AgentProfile,
        PersonalitySpec,
        RiskSpec,
        RoleSpec,
        ToolPostureSpec,
    )

    default_profile = AgentProfile(
        agent_id=agent_id,
        display_name=agent_id,
        profile_revision=1,
        role=RoleSpec(mission=f"I am {agent_id}, a pragmatic AI assistant."),
        personality=PersonalitySpec(tone="professional", verbosity="normal"),
        risk=RiskSpec(risk_level="medium", confirm_before=["destructive_actions"]),
        tool_posture=ToolPostureSpec(tool_use="allowed"),
    )
    try:
        repository.upsert_profile(default_profile)
        profile = repository.get_profile(agent_id)
    except Exception as exc:
        raise ToolRuntimeError(
            "EXEC_ERROR",
            "Failed to create identity profile",
            {"reason_code": LOCATION_REASON_STORAGE_EXEC_ERROR, "reason": str(exc)},
        ) from exc
    if profile is None:
        raise ToolRuntimeError(
            "NOT_FOUND",
            f"identity profile not found for agent_id={agent_id}",
            {"reason_code": LOCATION_REASON_RECORD_NOT_FOUND, "agent_id": agent_id},
        )
    return profile


def _set_identity_default_location(
    ctx: RuntimeContext,
    *,
    city: str,
    region: str | None,
    country: str | None,
    timezone_name: str | None,
    privacy_level: str,
) -> tuple[dict[str, Any], str]:
    repository = resolve_identity_repository(ctx)
    if repository is None:
        raise _identity_dependency_error(ctx)
    agent_id = _agent_id_from_context(ctx)
    profile = _ensure_identity_profile(repository, agent_id=agent_id)

    updated_meta = dict(getattr(profile, "meta", {}) or {})
    updated_home = {
        "city": city,
        "region": region,
        "country": country,
        "timezone": timezone_name,
    }
    updated_meta["home_location"] = updated_home
    updated_meta["location_privacy_level"] = privacy_level
    updated_meta["location_updated_at"] = _utc_now()
    updated_meta["location_city"] = city
    if region:
        updated_meta["location_region"] = region
    else:
        updated_meta.pop("location_region", None)
    if country:
        updated_meta["location_country"] = country
    else:
        updated_meta.pop("location_country", None)
    if timezone_name:
        updated_meta["location_timezone"] = timezone_name
    else:
        updated_meta.pop("location_timezone", None)

    revised = profile.model_copy(
        deep=True,
        update={
            "profile_revision": int(profile.profile_revision) + 1,
            "meta": updated_meta,
        },
    )
    try:
        profile_version = str(repository.upsert_profile(revised))
    except Exception as exc:
        raise ToolRuntimeError(
            "EXEC_ERROR",
            "Failed to persist identity profile update",
            {"reason_code": LOCATION_REASON_STORAGE_EXEC_ERROR, "reason": str(exc)},
        ) from exc

    record = _normalize_location_record(updated_home)
    record["identity_version"] = int(revised.profile_revision)
    identity_hash = (
        profile_version
        if profile_version.startswith("sha256:")
        else f"sha256:{profile_version}"
    )
    record["identity_hash"] = identity_hash
    record["agent_id"] = agent_id
    return record, profile_version


def _location_from_session_override(ctx: RuntimeContext) -> dict[str, Any] | None:
    raw = getattr(getattr(ctx, "policy", None), "raw", {})
    if not isinstance(raw, Mapping):
        return None
    context_meta = raw.get("context_metadata")
    if not isinstance(context_meta, Mapping):
        return None

    nested_candidates = (
        context_meta.get("location_override"),
        context_meta.get("session_location"),
    )
    for candidate in nested_candidates:
        if isinstance(candidate, Mapping):
            record = _normalize_location_record(candidate)
            if _has_location_data(record):
                return record

    flat_payload: dict[str, Any] = {}
    for source_key, target_key in (
        ("session_location_city", "city"),
        ("session_location_region", "region"),
        ("session_location_country", "country"),
        ("session_location_timezone", "timezone"),
        ("session_location_lat", "lat"),
        ("session_location_lon", "lon"),
    ):
        value = context_meta.get(source_key)
        if value not in (None, ""):
            flat_payload[target_key] = value
    record = _normalize_location_record(flat_payload)
    if _has_location_data(record):
        return record
    return None


def _lookup_ip_location_with_error(
    ctx: RuntimeContext,
    *,
    refresh: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    cfg = _location_policy_config(ctx)
    if not _policy_flag(cfg, "ip_lookup_enabled", default=True):
        return None, "NETWORK_DENIED"
    ttl_seconds = int(cfg.get("ip_cache_ttl_seconds", 3600) or 3600)
    allow_precise = _policy_flag(cfg, "allow_precise", default=False)
    timeout_seconds = float(cfg.get("ip_lookup_timeout_seconds", 2.5) or 2.5)
    retries = int(cfg.get("ip_lookup_retries", 1) or 1)
    retries = max(0, min(retries, 5))
    try:
        lookup_url = _validate_ip_lookup_url(
            str(
                cfg.get("ip_lookup_url", LOCATION_IP_FALLBACK_URL)
                or LOCATION_IP_FALLBACK_URL
            ),
            cfg=cfg,
        )
    except _NetworkPolicyError as exc:
        return None, exc.code

    now = time.time()
    cached_expires = float(_IP_CACHE.get("expires_at", 0.0) or 0.0)
    cached_record = _IP_CACHE.get("record")
    if not refresh and now < cached_expires and isinstance(cached_record, Mapping):
        return dict(cached_record), None

    payload: Mapping[str, Any] | None = None
    for attempt in range(retries + 1):
        request = urllib.request.Request(
            lookup_url,
            headers={
                "User-Agent": "OpenMinionLocation/1.0",
                "Accept": "application/json",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=max(0.1, timeout_seconds)
            ) as resp:
                decoded = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(decoded, Mapping):
                payload = decoded
                break
        except Exception:
            if attempt >= retries:
                return None, "IP_GEO_UNAVAILABLE"
            backoff = LOCATION_IP_RETRY_BACKOFF_SECONDS[
                min(attempt, len(LOCATION_IP_RETRY_BACKOFF_SECONDS) - 1)
            ]
            time.sleep(backoff)

    if not isinstance(payload, Mapping):
        return None, "IP_GEO_UNAVAILABLE"

    normalized = _normalize_location_record(
        {
            "city": payload.get("city"),
            "region": payload.get("region", payload.get("region_code")),
            "country": payload.get("country_name", payload.get("country_code")),
            "timezone": payload.get("timezone"),
            "lat": payload.get("latitude"),
            "lon": payload.get("longitude"),
        }
    )
    if not allow_precise:
        normalized["lat"] = None
        normalized["lon"] = None
    if not _has_location_data(normalized):
        return None, "IP_GEO_UNAVAILABLE"

    normalized["warnings"] = ["IP_GEO_IMPRECISE"]
    _IP_CACHE["record"] = dict(normalized)
    _IP_CACHE["expires_at"] = now + max(ttl_seconds, 1)
    return normalized, None


def _lookup_ip_location(
    ctx: RuntimeContext, *, refresh: bool = False
) -> dict[str, Any] | None:
    record, _ = _lookup_ip_location_with_error(ctx, refresh=refresh)
    return record


def _resolve_location(
    *,
    prefer: str,
    max_privacy: str,
    ctx: RuntimeContext,
    force_ip_only: bool = False,
    allow_ip_lookup: bool = True,
    refresh_ip_lookup: bool = False,
) -> dict[str, Any] | None:
    order: list[str]
    normalized_prefer = str(prefer or "auto").strip().lower() or "auto"
    if force_ip_only:
        order = ["ip.geo"]
    elif normalized_prefer == "session":
        order = ["session.override"]
    elif normalized_prefer == "identity":
        order = ["identity.default"]
    elif normalized_prefer == "ip":
        order = ["ip.geo"]
    else:
        order = ["session.override", "identity.default", "ip.geo"]

    for source in order:
        candidate: dict[str, Any] | None = None
        if source == "session.override":
            candidate = _location_from_session_override(ctx)
        elif source == "identity.default":
            candidate = _location_from_identity_profile(ctx)
        elif source == "ip.geo":
            if not allow_ip_lookup:
                continue
            candidate = _lookup_ip_location(ctx, refresh=refresh_ip_lookup)
        if candidate is None:
            continue
        applied = _apply_privacy(candidate, max_privacy=max_privacy)
        applied["source"] = source
        applied["confidence"] = _confidence_for_source(source)
        return applied
    return None


def _h_get(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(ctx, RuntimeContext):
        return _error(
            "RUNTIME_CONTEXT_REQUIRED",
            "location.get requires runtime context",
            method="location.get",
            source="none",
        )
    scope_error = _require_scope(ctx, required="READ_ONLY", method="location.get")
    if scope_error is not None:
        _emit_event(
            ctx,
            event_name="location.blocked",
            payload={"method": "location.get", "code": "POLICY_DENIED"},
        )
        return scope_error
    cfg = _location_policy_config(ctx)
    if not _policy_flag(cfg, "enabled", default=True):
        return _error(
            "POLICY_DENIED",
            "location tool is disabled by policy",
            method="location.get",
            source="none",
        )
    if not _policy_flag(cfg, "read_enabled", default=True):
        return _error(
            "POLICY_DENIED",
            "location.read is disabled by policy",
            method="location.get",
            source="none",
        )
    allow_ip_lookup = _policy_flag(cfg, "ip_lookup_enabled", default=True)
    prefer = str(args.get("prefer", "auto") or "auto").strip().lower() or "auto"
    max_privacy = (
        str(args.get("max_privacy", "city") or "city").strip().lower() or "city"
    )
    _emit_event(
        ctx,
        event_name="location.requested",
        payload={
            "method": "location.get",
            "prefer": prefer,
            "max_privacy": max_privacy,
        },
    )
    resolved = _resolve_location(
        prefer=prefer,
        max_privacy=max_privacy,
        ctx=ctx,
        allow_ip_lookup=allow_ip_lookup,
    )
    if resolved is None:
        return _location_unavailable_payload(ctx, max_privacy=max_privacy)
    return _resolved_location_payload(ctx, resolved, max_privacy=max_privacy)


def _location_unavailable_payload(
    ctx: RuntimeContext, *, max_privacy: str
) -> dict[str, Any]:
    # LSH: setup-oriented warning; structural checks only, no user-text heuristic.
    warnings_list: list[str] = []
    if (
        _location_from_session_override(ctx) is None
        and _location_from_identity_profile(ctx) is None
    ):
        warnings_list.append("LOCATION_NOT_CONFIGURED")
    warnings_list.append("LOCATION_UNAVAILABLE")
    payload = _success(
        method="location.get",
        source="none",
        privacy_level=max_privacy,
        confidence="low",
        city=None,
        region=None,
        country=None,
        timezone_name=None,
        lat=None,
        lon=None,
        warnings=warnings_list,
    )
    _emit_event(
        ctx,
        event_name="location.resolved",
        payload={"method": "location.get", "source": "none", "confidence": "low"},
    )
    return payload


def _resolved_location_payload(
    ctx: RuntimeContext, resolved: Mapping[str, Any], *, max_privacy: str
) -> dict[str, Any]:
    payload = _success(
        method="location.get",
        source=str(resolved.get("source", "none")),
        privacy_level=max_privacy,
        confidence=str(resolved.get("confidence", "low")),
        city=resolved.get("city"),
        region=resolved.get("region"),
        country=resolved.get("country"),
        timezone_name=resolved.get("timezone"),
        lat=resolved.get("lat"),
        lon=resolved.get("lon"),
        warnings=list(resolved.get("warnings", []) or []),
    )
    _emit_event(
        ctx,
        event_name="location.resolved",
        payload={
            "method": "location.get",
            "source": str(resolved.get("source", "none")),
            "confidence": str(resolved.get("confidence", "low")),
            "warnings": list(resolved.get("warnings", []) or []),
        },
    )
    return payload


def _set_default_preflight(
    ctx: Any,
) -> tuple[RuntimeContext | None, dict[str, Any] | None]:
    if not isinstance(ctx, RuntimeContext):
        return None, _error(
            "RUNTIME_CONTEXT_REQUIRED",
            "location.set_default requires runtime context",
            method="location.set_default",
            source="none",
        )
    scope_error = _require_scope(
        ctx, required="WRITE_SAFE", method="location.set_default"
    )
    if scope_error is not None:
        _emit_event(
            ctx,
            event_name="location.blocked",
            payload={"method": "location.set_default", "code": "POLICY_DENIED"},
        )
        return ctx, scope_error
    cfg = _location_policy_config(ctx)
    if not _policy_flag(cfg, "enabled", default=True):
        return ctx, _error(
            "POLICY_DENIED",
            "location tool is disabled by policy",
            method="location.set_default",
            source="none",
        )
    if not _policy_flag(cfg, "write_enabled", default=True):
        return ctx, _error(
            "POLICY_DENIED",
            "location.write is disabled by policy",
            method="location.set_default",
            source="none",
        )
    if _policy_flag(cfg, "require_confirm_for_set_default", default=True) and not bool(
        ctx.confirm
    ):
        _emit_event(
            ctx,
            event_name="location.blocked",
            payload={"method": "location.set_default", "code": "CONFIRM_REQUIRED"},
        )
        return ctx, _error(
            "CONFIRM_REQUIRED",
            "location.set_default requires explicit confirmation",
            method="location.set_default",
            source="none",
            details={"suggestion": "Retry with meta.confirm=true or --confirm"},
        )
    return ctx, None


def _persist_default_location_result(
    ctx: RuntimeContext,
    *,
    city: str,
    region: str | None,
    country: str | None,
    timezone_name: str | None,
    privacy_level: str,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
    try:
        record, profile_version = _set_identity_default_location(
            ctx,
            city=city,
            region=region,
            country=country,
            timezone_name=timezone_name,
            privacy_level=privacy_level,
        )
    except ToolRuntimeError as exc:
        _emit_event(
            ctx,
            event_name="location.blocked",
            payload={"method": "location.set_default", "code": exc.code},
        )
        return (
            None,
            None,
            _error(
                exc.code,
                exc.message,
                method="location.set_default",
                source="none",
                details=dict(exc.details or {}),
            ),
        )
    except Exception as exc:
        _emit_event(
            ctx,
            event_name="location.blocked",
            payload={"method": "location.set_default", "code": "EXEC_ERROR"},
        )
        return (
            None,
            None,
            _error(
                "EXEC_ERROR",
                "Failed to persist default location",
                method="location.set_default",
                source="none",
                details={
                    "reason": str(exc),
                    "reason_code": LOCATION_REASON_STORAGE_EXEC_ERROR,
                },
            ),
        )
    return record, profile_version, None


def _h_set_default(_args: dict[str, Any], _ctx: Any) -> dict[str, Any]:
    ctx, preflight_error = _set_default_preflight(_ctx)
    if preflight_error is not None:
        return preflight_error
    assert ctx is not None
    city, region, country, timezone_name, privacy_level, args_error = (
        _location_set_default_args(_args)
    )
    if args_error is not None:
        return args_error
    _emit_event(
        ctx,
        event_name="location.requested",
        payload={
            "method": "location.set_default",
            "city": city,
            "region": region,
            "country": country,
            "privacy_level": privacy_level,
        },
    )
    record, profile_version, persist_error = _persist_default_location_result(
        ctx,
        city=city,
        region=region,
        country=country,
        timezone_name=timezone_name,
        privacy_level=privacy_level,
    )
    if persist_error is not None:
        return persist_error
    assert record is not None and profile_version is not None

    identity_version = int(record.get("identity_version", 0) or 0)
    identity_hash = str(record.get("identity_hash", "") or "")
    if not identity_hash:
        identity_hash = (
            profile_version
            if str(profile_version).startswith("sha256:")
            else f"sha256:{profile_version}"
        )
    agent_id = str(record.get("agent_id", "") or _agent_id_from_context(ctx))
    _emit_event(
        ctx,
        event_name="location.default.updated",
        payload={
            "method": "location.set_default",
            "agent_id": agent_id,
            "identity_version": identity_version,
            "identity_hash": identity_hash,
        },
    )
    return _success_set_default(
        city=str(record.get("city", city) or city),
        region=record.get("region"),
        country=record.get("country"),
        timezone_name=record.get("timezone"),
        privacy_level=privacy_level,
        identity_version=max(identity_version, 1),
        identity_hash=identity_hash,
        agent_id=agent_id,
    )


def _h_get_ip(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    if not isinstance(ctx, RuntimeContext):
        return _error(
            "RUNTIME_CONTEXT_REQUIRED",
            "location.get_ip requires runtime context",
            method="location.get_ip",
            source="none",
        )
    scope_error = _require_scope(ctx, required="READ_ONLY", method="location.get_ip")
    if scope_error is not None:
        _emit_event(
            ctx,
            event_name="location.blocked",
            payload={"method": "location.get_ip", "code": "POLICY_DENIED"},
        )
        return scope_error
    cfg = _location_policy_config(ctx)
    if not _policy_flag(cfg, "enabled", default=True):
        return _error(
            "POLICY_DENIED",
            "location tool is disabled by policy",
            method="location.get_ip",
            source="none",
        )
    if not _policy_flag(cfg, "ip_lookup_enabled", default=True):
        return _error(
            "POLICY_DENIED",
            "location.ip.read is disabled by policy",
            method="location.get_ip",
            source="none",
        )
    max_privacy = (
        str(args.get("max_privacy", "city") or "city").strip().lower() or "city"
    )
    refresh = bool(args.get("refresh", False))
    _emit_event(
        ctx,
        event_name="location.requested",
        payload={
            "method": "location.get_ip",
            "prefer": "ip",
            "max_privacy": max_privacy,
        },
    )
    resolved_raw, lookup_error = _lookup_ip_location_with_error(ctx, refresh=refresh)
    resolved: dict[str, Any] | None = None
    if resolved_raw is not None:
        resolved = _apply_privacy(resolved_raw, max_privacy=max_privacy)
        resolved["source"] = "ip.geo"
        resolved["confidence"] = _confidence_for_source("ip.geo")
    if resolved is None:
        error_code = str(lookup_error or "IP_GEO_UNAVAILABLE")
        message = "IP geolocation backend unavailable"
        if error_code == "NETWORK_DENIED":
            message = "IP geolocation backend blocked by network policy"
        _emit_event(
            ctx,
            event_name="location.blocked",
            payload={"method": "location.get_ip", "code": error_code},
        )
        return _error(
            error_code,
            message,
            method="location.get_ip",
            source="none",
        )
    payload = _success(
        method="location.get_ip",
        source="ip.geo",
        privacy_level=max_privacy,
        confidence=str(resolved.get("confidence", "low")),
        city=resolved.get("city"),
        region=resolved.get("region"),
        country=resolved.get("country"),
        timezone_name=resolved.get("timezone"),
        lat=resolved.get("lat"),
        lon=resolved.get("lon"),
        warnings=list(resolved.get("warnings", []) or []),
    )
    _emit_event(
        ctx,
        event_name="location.resolved",
        payload={
            "method": "location.get_ip",
            "source": "ip.geo",
            "confidence": str(resolved.get("confidence", "low")),
            "warnings": list(resolved.get("warnings", []) or []),
        },
    )
    return payload


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name="location.get",
            args_model=LocationGetArgs,
            min_scope="READ_ONLY",
            handler=_h_get,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "location"),
            capabilities=("read_only", "location"),
        )
    )
    registry.add(
        ToolSpec(
            name="location.set_default",
            args_model=LocationSetDefaultArgs,
            min_scope="WRITE_SAFE",
            handler=_h_set_default,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "location"),
            capabilities=("write_safe", "location"),
        )
    )
    registry.add(
        ToolSpec(
            name="location.get_ip",
            args_model=LocationGetIPArgs,
            min_scope="READ_ONLY",
            handler=_h_get_ip,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "location"),
            capabilities=("read_only", "location"),
        )
    )


__all__ = ["register"]
