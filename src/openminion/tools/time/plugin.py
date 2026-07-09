import time
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openminion.services.cron.scheduling import compute_next_due
from openminion.modules.tool.runtime.environment import (
    agent_id_from_context as _agent_id_from_context,
)
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime import RuntimeContext, resolve_identity_repository
from .args import (
    TimeConvertArgs,
    TimeDayBoundaryArgs,
    TimeDiffArgs,
    TimeFormatArgs,
    TimeInZoneArgs,
    TimeNextCronArgs,
    TimeNowArgs,
    TimeParseISOArgs,
)
from .constants import (
    DEFAULT_NEXT_CRON_COUNT,
    DEFAULT_PARSE_TIMEZONE,
    MAX_NEXT_CRON_COUNT,
    OPENMINION_TIMEZONE_ENV,
    TIME_REASON_STORAGE_EXEC_ERROR,
    TIME_REASON_STORAGE_UNAVAILABLE,
    TIME_REASON_STORAGE_UNCONFIGURED,
    TIMEZONE_META_KEYS,
)

_UTC = timezone.utc
_TIMEZONE_META_KEYS = TIMEZONE_META_KEYS


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(_UTC).isoformat().replace("+00:00", "Z")


def _iso_local(dt: datetime) -> str:
    return dt.isoformat()


def _load_timezone(name: str) -> ZoneInfo:
    token = str(name or "").strip()
    if not token:
        raise ToolRuntimeError(
            "INVALID_TIMEZONE",
            "timezone is required",
            {"timezone": token},
        )
    try:
        return ZoneInfo(token)
    except ZoneInfoNotFoundError as exc:
        raise ToolRuntimeError(
            "INVALID_TIMEZONE",
            f"unknown timezone: {token}",
            {"timezone": token},
        ) from exc


# TGFC: stable provider id for the time tool family. Single canonical owner.
_TIME_TOOL_SOURCE = "time_module"


class _WeatherTimezoneResolver(Protocol):
    def resolve_openmeteo_config(self, ctx: RuntimeContext) -> Any: ...

    def geocode_openmeteo_location(
        self,
        query: str,
        *,
        config: Any,
        language: str,
        timeout_s: float,
    ) -> tuple[Mapping[str, Any], str, Mapping[str, Any]]: ...

    def secondary_geocode_openmeteo_location(
        self,
        query: str,
        *,
        config: Any,
        language: str,
        timeout_s: float,
    ) -> tuple[Mapping[str, Any], str, list[Mapping[str, Any]]]: ...

    def forecast_openmeteo_current(
        self,
        *,
        latitude: float,
        longitude: float,
        config: Any,
        timeout_s: float,
    ) -> tuple[Mapping[str, Any], str, Mapping[str, Any]]: ...


def _build_instant(*, dt_utc: datetime, timezone_name: str) -> dict[str, Any]:
    utc_dt = dt_utc.astimezone(_UTC)
    zone = _load_timezone(timezone_name)
    local_dt = utc_dt.astimezone(zone)
    offset = local_dt.utcoffset() or timedelta(0)
    unix_ts = utc_dt.timestamp()
    return {
        "utc": _iso_utc(utc_dt),
        "unix_seconds": int(unix_ts),
        "unix_millis": int(unix_ts * 1000),
        "timezone": timezone_name,
        "local": _iso_local(local_dt),
        "offset_seconds": int(offset.total_seconds()),
        "source": _TIME_TOOL_SOURCE,
    }


def _contains_time_component(raw_iso: str) -> bool:
    token = str(raw_iso or "").strip()
    if not token:
        return False
    if "T" in token or " " in token:
        return True
    return False


def _parse_iso8601(
    *, iso: str, timezone_hint: str | None = None
) -> tuple[datetime, bool]:
    token = str(iso or "").strip()
    if not token:
        raise ToolRuntimeError("INVALID_ISO8601", "iso is required", {"iso": token})
    if not _contains_time_component(token):
        raise ToolRuntimeError(
            "INVALID_ISO8601",
            "timestamp must include date and time",
            {"iso": token},
        )
    normalized = token.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ToolRuntimeError(
            "INVALID_ISO8601",
            "failed to parse ISO8601 timestamp",
            {"iso": token},
        ) from exc
    assumed_timezone = False
    if parsed.tzinfo is None:
        assumed_timezone = True
        hint = str(timezone_hint or "").strip() or DEFAULT_PARSE_TIMEZONE
        parsed = parsed.replace(tzinfo=_load_timezone(hint))
    return parsed.astimezone(_UTC), assumed_timezone


def _timezone_from_identity_profile(ctx: RuntimeContext) -> str | None:
    repository = resolve_identity_repository(ctx)
    if repository is None:
        identity_path = getattr(ctx.repositories, "identity_path", None)
        if identity_path is None:
            raise ToolRuntimeError(
                "DEPENDENCY_MISSING",
                "Identity storage is not configured",
                {"reason_code": TIME_REASON_STORAGE_UNCONFIGURED},
            )
        raise ToolRuntimeError(
            "DEPENDENCY_MISSING",
            "Identity storage is unavailable",
            {
                "reason_code": TIME_REASON_STORAGE_UNAVAILABLE,
                "identity_path": str(identity_path),
            },
        )
    agent_id = _agent_id_from_context(ctx)
    try:
        profile = repository.get_profile(agent_id)
    except Exception as exc:
        raise ToolRuntimeError(
            "EXEC_ERROR",
            "Failed to load identity profile timezone",
            {"reason_code": TIME_REASON_STORAGE_EXEC_ERROR, "reason": str(exc)},
        ) from exc
    if profile is None:
        return None
    metadata = getattr(profile, "meta", None)
    if not isinstance(metadata, Mapping):
        return None
    for key in _TIMEZONE_META_KEYS:
        token = str(metadata.get(key, "")).strip()
        if not token:
            continue
        try:
            _load_timezone(token)
        except ToolRuntimeError:
            continue
        return token
    return None


def _timezone_from_context_metadata(ctx: RuntimeContext) -> str | None:
    policy_raw = getattr(ctx.policy, "raw", {}) or {}
    if not isinstance(policy_raw, Mapping):
        return None
    context_meta = policy_raw.get("context_metadata")
    if not isinstance(context_meta, Mapping):
        return None
    for key in _TIMEZONE_META_KEYS:
        token = str(context_meta.get(key, "")).strip()
        if not token:
            continue
        try:
            _load_timezone(token)
        except ToolRuntimeError:
            continue
        return token
    return None


def _timezone_from_location_fallback(ctx: RuntimeContext) -> str | None:
    try:
        from openminion.tools.location.plugin import resolve_location_for_tool
    except Exception:
        return None

    try:
        resolved = resolve_location_for_tool(
            prefer="auto",
            max_privacy="city",
            ctx=ctx,
            allow_ip_lookup=True,
        )
    except Exception:
        return None
    if not isinstance(resolved, Mapping):
        return None
    token = str(resolved.get("timezone", "")).strip()
    if not token:
        return None
    try:
        _load_timezone(token)
    except ToolRuntimeError:
        return None
    return token


def _timezone_from_geocode_payload(payload: Mapping[str, Any]) -> str | None:
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    first = results[0] if isinstance(results[0], Mapping) else {}
    token = str(first.get("timezone", "")).strip()
    if not token:
        return None
    try:
        _load_timezone(token)
    except ToolRuntimeError:
        return None
    return token


def _coordinates_from_location_record(
    location: Mapping[str, Any],
) -> tuple[float, float] | None:
    try:
        latitude = float(location.get("latitude"))
        longitude = float(location.get("longitude"))
    except (TypeError, ValueError):
        return None
    return latitude, longitude


def _timezone_from_forecast_payload(payload: Mapping[str, Any]) -> str | None:
    token = str(payload.get("timezone", "")).strip()
    if not token:
        return None
    try:
        _load_timezone(token)
    except ToolRuntimeError:
        return None
    return token


def _weather_plugin_and_config(
    query: str, ctx: RuntimeContext
) -> tuple[_WeatherTimezoneResolver, Any]:
    try:
        from openminion.tools.weather.providers.openmeteo import (
            plugin as weather_plugin,
        )
    except Exception as exc:
        raise ToolRuntimeError(
            "DEPENDENCY_MISSING",
            "location-to-timezone resolution is unavailable",
            {"location": query},
        ) from exc
    try:
        config = weather_plugin.resolve_openmeteo_config(ctx)
    except Exception as exc:
        raise ToolRuntimeError(
            "EXEC_ERROR",
            "failed to load location-to-timezone resolution config",
            {"location": query},
        ) from exc
    return weather_plugin, config


def _timezone_from_resolved_location(
    *,
    resolved: Mapping[str, Any],
    timezone_payload: Mapping[str, Any] | None = None,
    weather_plugin: _WeatherTimezoneResolver,
    config: Any,
    timeout_s: float,
    query: str,
) -> str:
    timezone_name = _timezone_from_geocode_payload(timezone_payload or resolved)
    if timezone_name:
        return timezone_name
    coordinates = _coordinates_from_location_record(resolved)
    if coordinates is None:
        raise ToolRuntimeError(
            "INVALID_RESPONSE",
            "location resolution payload missing latitude/longitude",
            {"location": query},
        )
    config_auto_timezone = config.model_copy(update={"timezone": "auto"})
    _current, _forecast_url, forecast_payload = (
        weather_plugin.forecast_openmeteo_current(
            latitude=coordinates[0],
            longitude=coordinates[1],
            config=config_auto_timezone,
            timeout_s=timeout_s,
        )
    )
    timezone_name = _timezone_from_forecast_payload(forecast_payload)
    if timezone_name:
        return timezone_name
    raise ToolRuntimeError(
        "INVALID_RESPONSE",
        "location resolution payload missing timezone",
        {"location": query},
    )


def _secondary_geocode_timezone(
    *,
    weather_plugin: _WeatherTimezoneResolver,
    config: Any,
    language: str,
    timeout_s: float,
    query: str,
    primary_error: ToolRuntimeError | None,
) -> str:
    try:
        resolved, _secondary_url, _secondary_payload = (
            weather_plugin.secondary_geocode_openmeteo_location(
                query,
                config=config,
                language=language,
                timeout_s=timeout_s,
            )
        )
    except ToolRuntimeError as exc:
        details = {"location": query, **dict(exc.details or {})}
        if primary_error is not None:
            details["primary_error_code"] = primary_error.code
        raise ToolRuntimeError(exc.code, exc.message, details) from exc
    return _timezone_from_resolved_location(
        resolved=resolved,
        weather_plugin=weather_plugin,
        config=config,
        timeout_s=timeout_s,
        query=query,
    )


def _timezone_from_explicit_location(location: str, ctx: RuntimeContext) -> str:
    query = str(location or "").strip()
    if not query:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "location is required",
            {"location": query},
        )
    weather_plugin, config = _weather_plugin_and_config(query, ctx)

    timeout_s = float(getattr(config, "timeout_seconds", 5.0) or 5.0)
    language = str(getattr(config, "default_language", "en") or "en").strip() or "en"

    primary_error: ToolRuntimeError | None = None
    try:
        resolved, _geocode_url, geocode_payload = (
            weather_plugin.geocode_openmeteo_location(
                query,
                config=config,
                language=language,
                timeout_s=timeout_s,
            )
        )
    except ToolRuntimeError as exc:
        primary_error = exc
    else:
        return _timezone_from_resolved_location(
            resolved=resolved,
            timezone_payload=geocode_payload
            if isinstance(geocode_payload, Mapping)
            else None,
            weather_plugin=weather_plugin,
            config=config,
            timeout_s=timeout_s,
            query=query,
        )

    if primary_error is not None and primary_error.code != "NOT_FOUND":
        raise ToolRuntimeError(
            primary_error.code,
            primary_error.message,
            {"location": query, **dict(primary_error.details or {})},
        ) from primary_error

    return _secondary_geocode_timezone(
        weather_plugin=weather_plugin,
        config=config,
        language=language,
        timeout_s=timeout_s,
        query=query,
        primary_error=primary_error,
    )


def _resolve_timezone(
    *,
    explicit_timezone: str | None,
    explicit_location: str | None,
    ctx: RuntimeContext,
) -> tuple[str, bool]:
    explicit = str(explicit_timezone or "").strip()
    if explicit:
        _load_timezone(explicit)
        return explicit, False
    explicit_location_token = str(explicit_location or "").strip()
    if explicit_location_token:
        return _timezone_from_explicit_location(explicit_location_token, ctx), False
    identity_timezone = _timezone_from_identity_profile(ctx)
    if identity_timezone:
        return identity_timezone, True
    metadata_timezone = _timezone_from_context_metadata(ctx)
    if metadata_timezone:
        return metadata_timezone, False
    location_timezone = _timezone_from_location_fallback(ctx)
    if location_timezone:
        return location_timezone, False
    env_timezone = (
        str(ctx.env.get(OPENMINION_TIMEZONE_ENV, "")).strip()
        or str(ctx.env.get("TZ", "")).strip()
    )
    if env_timezone:
        try:
            _load_timezone(env_timezone)
        except ToolRuntimeError:
            return "UTC", False
        return env_timezone, False
    return "UTC", False


def _normalize_value(value: float) -> int | float:
    if float(value).is_integer():
        return int(value)
    return float(value)


def _record_tool_call(
    *,
    ctx: RuntimeContext,
    method: str,
    started_at: float,
    timezone_name: str,
    defaulted_from_identity: bool,
    error_code: str = "",
) -> None:
    ctx.add_log(
        "info",
        f"{method} completed",
        {
            "tool": method,
            "timezone": timezone_name,
            "duration_ms": int(max(0.0, (time.perf_counter() - started_at) * 1000.0)),
            "defaulted_from_identity": bool(defaulted_from_identity),
            "error_code": error_code,
        },
    )


def _h_now(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    started_at = time.perf_counter()
    timezone_name = "UTC"
    defaulted_from_identity = False
    error_code = ""
    try:
        timezone_name, defaulted_from_identity = _resolve_timezone(
            explicit_timezone=str(args.get("timezone") or "").strip() or None,
            explicit_location=str(args.get("location") or "").strip() or None,
            ctx=ctx,
        )
        return _build_instant(dt_utc=datetime.now(_UTC), timezone_name=timezone_name)
    except ToolRuntimeError as exc:
        error_code = exc.code
        raise
    finally:
        _record_tool_call(
            ctx=ctx,
            method="time.now",
            started_at=started_at,
            timezone_name=timezone_name,
            defaulted_from_identity=defaulted_from_identity,
            error_code=error_code,
        )


def _h_in_zone(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    started_at = time.perf_counter()
    timezone_name = "UTC"
    error_code = ""
    try:
        timezone_name = str(args.get("timezone") or "").strip()
        _load_timezone(timezone_name)
        return _build_instant(dt_utc=datetime.now(_UTC), timezone_name=timezone_name)
    except ToolRuntimeError as exc:
        error_code = exc.code
        raise
    finally:
        _record_tool_call(
            ctx=ctx,
            method="time.in_zone",
            started_at=started_at,
            timezone_name=timezone_name or "UTC",
            defaulted_from_identity=False,
            error_code=error_code,
        )


def _h_convert(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    started_at = time.perf_counter()
    timezone_name = "UTC"
    error_code = ""
    try:
        timezone_name = str(args.get("to_timezone") or "").strip()
        _load_timezone(timezone_name)
        dt_utc, _ = _parse_iso8601(iso=str(args.get("iso") or "").strip())
        return _build_instant(dt_utc=dt_utc, timezone_name=timezone_name)
    except ToolRuntimeError as exc:
        error_code = exc.code
        raise
    finally:
        _record_tool_call(
            ctx=ctx,
            method="time.convert",
            started_at=started_at,
            timezone_name=timezone_name,
            defaulted_from_identity=False,
            error_code=error_code,
        )


def _h_parse_iso(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    started_at = time.perf_counter()
    timezone_name = "UTC"
    error_code = ""
    assumed_timezone = False
    try:
        raw_iso = str(args.get("iso") or "").strip()
        timezone_hint = str(args.get("timezone_hint") or "").strip() or None
        dt_utc, assumed_timezone = _parse_iso8601(
            iso=raw_iso, timezone_hint=timezone_hint
        )
        if assumed_timezone and timezone_hint:
            timezone_name = timezone_hint
        else:
            timezone_name = "UTC"
        return {
            "instant": _build_instant(dt_utc=dt_utc, timezone_name=timezone_name),
            "assumed_timezone": assumed_timezone,
            "source": _TIME_TOOL_SOURCE,
        }
    except ToolRuntimeError as exc:
        error_code = exc.code
        raise
    finally:
        _record_tool_call(
            ctx=ctx,
            method="time.parse_iso",
            started_at=started_at,
            timezone_name=timezone_name,
            defaulted_from_identity=False,
            error_code=error_code,
        )


def _h_diff(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    started_at = time.perf_counter()
    error_code = ""
    unit = str(args.get("unit") or "seconds").strip().lower() or "seconds"
    try:
        a_utc, _ = _parse_iso8601(iso=str(args.get("a") or "").strip())
        b_utc, _ = _parse_iso8601(iso=str(args.get("b") or "").strip())
        unit_divisor = {
            "seconds": 1.0,
            "minutes": 60.0,
            "hours": 3600.0,
            "days": 86400.0,
        }.get(unit)
        if unit_divisor is None:
            raise ToolRuntimeError(
                "OUT_OF_RANGE",
                f"unsupported unit: {unit}",
                {"unit": unit, "supported": ["seconds", "minutes", "hours", "days"]},
            )
        seconds = (b_utc - a_utc).total_seconds()
        if bool(args.get("abs", True)):
            seconds = abs(seconds)
        return {
            "seconds": _normalize_value(seconds),
            "unit": unit,
            "value": _normalize_value(seconds / unit_divisor),
            "source": _TIME_TOOL_SOURCE,
            "a_utc": _iso_utc(a_utc),
            "b_utc": _iso_utc(b_utc),
        }
    except ToolRuntimeError as exc:
        error_code = exc.code
        raise
    finally:
        _record_tool_call(
            ctx=ctx,
            method="time.diff",
            started_at=started_at,
            timezone_name="UTC",
            defaulted_from_identity=False,
            error_code=error_code,
        )


def _h_format(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    started_at = time.perf_counter()
    timezone_name = "UTC"
    error_code = ""
    try:
        dt_utc, _ = _parse_iso8601(iso=str(args.get("iso") or "").strip())
        timezone_name = str(args.get("timezone") or "").strip() or "UTC"
        zone = _load_timezone(timezone_name)
        local_dt = dt_utc.astimezone(zone)
        fmt = str(args.get("format") or "iso").strip().lower() or "iso"
        custom_pattern = str(args.get("custom") or "").strip()
        if fmt in {"iso", "rfc3339"}:
            formatted = _iso_local(local_dt).replace("+00:00", "Z")
        elif fmt == "date":
            formatted = local_dt.strftime("%Y-%m-%d")
        elif fmt == "time":
            formatted = local_dt.strftime("%H:%M:%S")
        elif fmt == "datetime":
            formatted = local_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        elif fmt == "custom":
            if not custom_pattern:
                raise ToolRuntimeError(
                    "OUT_OF_RANGE",
                    "custom format requires `custom` pattern",
                    {"format": fmt},
                )
            formatted = local_dt.strftime(custom_pattern)
        else:
            raise ToolRuntimeError(
                "OUT_OF_RANGE",
                f"unsupported format: {fmt}",
                {
                    "format": fmt,
                    "supported": [
                        "iso",
                        "rfc3339",
                        "date",
                        "time",
                        "datetime",
                        "custom",
                    ],
                },
            )
        return {"formatted": formatted, "source": _TIME_TOOL_SOURCE}
    except ToolRuntimeError as exc:
        error_code = exc.code
        raise
    finally:
        _record_tool_call(
            ctx=ctx,
            method="time.format",
            started_at=started_at,
            timezone_name=timezone_name,
            defaulted_from_identity=False,
            error_code=error_code,
        )


def _h_start_of_day(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    started_at = time.perf_counter()
    timezone_name = "UTC"
    defaulted_from_identity = False
    error_code = ""
    try:
        timezone_name, defaulted_from_identity = _resolve_timezone(
            explicit_timezone=str(args.get("timezone") or "").strip() or None,
            explicit_location=None,
            ctx=ctx,
        )
        zone = _load_timezone(timezone_name)
        raw_iso = str(args.get("iso") or "").strip()
        if raw_iso:
            dt_utc, _ = _parse_iso8601(iso=raw_iso, timezone_hint=timezone_name)
        else:
            dt_utc = datetime.now(_UTC)
        local_dt = dt_utc.astimezone(zone)
        start_local = local_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return {
            "start": _build_instant(
                dt_utc=start_local.astimezone(_UTC), timezone_name=timezone_name
            ),
            "source": _TIME_TOOL_SOURCE,
        }
    except ToolRuntimeError as exc:
        error_code = exc.code
        raise
    finally:
        _record_tool_call(
            ctx=ctx,
            method="time.start_of_day",
            started_at=started_at,
            timezone_name=timezone_name,
            defaulted_from_identity=defaulted_from_identity,
            error_code=error_code,
        )


def _h_end_of_day(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    started_at = time.perf_counter()
    timezone_name = "UTC"
    defaulted_from_identity = False
    error_code = ""
    try:
        timezone_name, defaulted_from_identity = _resolve_timezone(
            explicit_timezone=str(args.get("timezone") or "").strip() or None,
            explicit_location=None,
            ctx=ctx,
        )
        zone = _load_timezone(timezone_name)
        raw_iso = str(args.get("iso") or "").strip()
        if raw_iso:
            dt_utc, _ = _parse_iso8601(iso=raw_iso, timezone_hint=timezone_name)
        else:
            dt_utc = datetime.now(_UTC)
        local_dt = dt_utc.astimezone(zone)
        end_local = local_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        return {
            "end": _build_instant(
                dt_utc=end_local.astimezone(_UTC), timezone_name=timezone_name
            ),
            "source": _TIME_TOOL_SOURCE,
        }
    except ToolRuntimeError as exc:
        error_code = exc.code
        raise
    finally:
        _record_tool_call(
            ctx=ctx,
            method="time.end_of_day",
            started_at=started_at,
            timezone_name=timezone_name,
            defaulted_from_identity=defaulted_from_identity,
            error_code=error_code,
        )


def _h_next_cron(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    started_at = time.perf_counter()
    timezone_name = "UTC"
    error_code = ""
    try:
        cron_expr = str(args.get("cron") or "").strip()
        timezone_name = str(args.get("timezone") or "").strip()
        _load_timezone(timezone_name)
        count = int(
            args.get("count", DEFAULT_NEXT_CRON_COUNT) or DEFAULT_NEXT_CRON_COUNT
        )
        if count < 1 or count > MAX_NEXT_CRON_COUNT:
            raise ToolRuntimeError(
                "OUT_OF_RANGE",
                f"count must be between 1 and {MAX_NEXT_CRON_COUNT}",
                {"count": count},
            )
        raw_from_iso = str(args.get("from_iso") or "").strip()
        if raw_from_iso:
            cursor, _ = _parse_iso8601(iso=raw_from_iso, timezone_hint=timezone_name)
        else:
            cursor = datetime.now(_UTC)
        schedule = {
            "kind": "cron",
            "expr": cron_expr,
            "tz": timezone_name,
            "stagger_ms": 0,
        }
        items: list[dict[str, Any]] = []
        for index in range(count):
            due = compute_next_due(
                schedule=schedule,
                after=cursor,
                job_id=f"time.next_cron:{cron_expr}:{index}",
            )
            if due is None:
                break
            due_utc = due.astimezone(_UTC)
            items.append(_build_instant(dt_utc=due_utc, timezone_name=timezone_name))
            cursor = due_utc + timedelta(seconds=1)
        return {
            "next": items,
            "count": len(items),
            "timezone": timezone_name,
            "cron": cron_expr,
            "source": _TIME_TOOL_SOURCE,
        }
    except ValueError as exc:
        error_code = "INVALID_ARGUMENT"
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            str(exc),
            {"cron": str(args.get("cron") or "")},
        ) from exc
    except ToolRuntimeError as exc:
        error_code = exc.code
        raise
    finally:
        _record_tool_call(
            ctx=ctx,
            method="time.next_cron",
            started_at=started_at,
            timezone_name=timezone_name,
            defaulted_from_identity=False,
            error_code=error_code,
        )


def _time_tool_specs() -> tuple[ToolSpec, ...]:
    return (
        ToolSpec(
            name="time.now",
            args_model=TimeNowArgs,
            min_scope="READ_ONLY",
            handler=_h_now,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "time"),
            capabilities=("read_only", "time"),
        ),
        ToolSpec(
            name="time.in_zone",
            args_model=TimeInZoneArgs,
            min_scope="READ_ONLY",
            handler=_h_in_zone,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "time"),
            capabilities=("read_only", "time"),
        ),
        ToolSpec(
            name="time.convert",
            args_model=TimeConvertArgs,
            min_scope="READ_ONLY",
            handler=_h_convert,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "time"),
            capabilities=("read_only", "time"),
        ),
        ToolSpec(
            name="time.parse_iso",
            args_model=TimeParseISOArgs,
            min_scope="READ_ONLY",
            handler=_h_parse_iso,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "time"),
            capabilities=("read_only", "time"),
        ),
        ToolSpec(
            name="time.diff",
            args_model=TimeDiffArgs,
            min_scope="READ_ONLY",
            handler=_h_diff,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "time"),
            capabilities=("read_only", "time"),
        ),
        ToolSpec(
            name="time.format",
            args_model=TimeFormatArgs,
            min_scope="READ_ONLY",
            handler=_h_format,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "time"),
            capabilities=("read_only", "time"),
        ),
        ToolSpec(
            name="time.start_of_day",
            args_model=TimeDayBoundaryArgs,
            min_scope="READ_ONLY",
            handler=_h_start_of_day,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "time"),
            capabilities=("read_only", "time"),
        ),
        ToolSpec(
            name="time.end_of_day",
            args_model=TimeDayBoundaryArgs,
            min_scope="READ_ONLY",
            handler=_h_end_of_day,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "time"),
            capabilities=("read_only", "time"),
        ),
        ToolSpec(
            name="time.next_cron",
            args_model=TimeNextCronArgs,
            min_scope="READ_ONLY",
            handler=_h_next_cron,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "time", "cron"),
            capabilities=("read_only", "time", "cron"),
        ),
    )


def register(registry: ToolRegistry) -> None:
    for spec in _time_tool_specs():
        registry.add(spec)


__all__ = ["register"]
