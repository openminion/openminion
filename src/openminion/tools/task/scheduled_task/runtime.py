from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from openminion.modules.task.scheduling.schedule import to_iso_utc, utc_now
from openminion.modules.tool.runtime.context import RuntimeContext

from ..constants import (
    CONSOLIDATION_PAYLOAD_KEY,
    DEFAULT_CONSOLIDATION_BATCH_LIMIT,
    DEFAULT_CONSOLIDATION_MAX_ITERATIONS,
    DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS,
    DEFAULT_WATCH_MAX_CHECKS,
    DEFAULT_WATCH_TIMEOUT_SECONDS,
    DEFAULT_WATCH_TTL_MINUTES,
    EVERY_UNIT_TO_MS,
    WATCH_PAYLOAD_KEY,
)


_EVERY_SCHEDULE_ALIASES: tuple[tuple[str, str | None], ...] = (
    ("interval", None),
    ("every", None),
    ("milliseconds", "milliseconds"),
    ("seconds", "seconds"),
    ("minutes", "minutes"),
    ("hours", "hours"),
    ("days", "days"),
    ("ms", "ms"),
    ("s", "s"),
    ("m", "m"),
    ("h", "h"),
    ("d", "d"),
    ("interval_milliseconds", "milliseconds"),
    ("interval_seconds", "seconds"),
    ("interval_minutes", "minutes"),
    ("interval_hours", "hours"),
    ("interval_days", "days"),
    ("every_milliseconds", "milliseconds"),
    ("every_seconds", "seconds"),
    ("every_minutes", "minutes"),
    ("every_hours", "hours"),
    ("every_days", "days"),
)
_EVERY_SCHEDULE_ALIAS_KEYS = tuple(key for key, _ in _EVERY_SCHEDULE_ALIASES)


def _safe_str(obj: Mapping[str, Any], key: str, default: str = "") -> str:
    return str(obj.get(key, default) or default).strip()


def _text(value: Any) -> str:
    return str(value).strip() if value else ""


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _every_unit_multiplier(unit: Any) -> int:
    token = _text(unit).lower()
    if not token:
        return EVERY_UNIT_TO_MS["seconds"]
    multiplier = EVERY_UNIT_TO_MS.get(token)
    if multiplier is None:
        raise ValueError(f"unsupported every unit: {unit}")
    return multiplier


def _coerce_schedule_aliases(schedule: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(schedule or {})
    kind = _safe_str(normalized, "kind")

    if kind == "cron":
        if normalized.get("expr") is None:
            for alias in ("expression", "cron_expr", "cron"):
                if normalized.get(alias) is None:
                    continue
                normalized["expr"] = normalized.get(alias)
                normalized.pop(alias, None)
                break
        if normalized.get("tz") is None and normalized.get("timezone") is not None:
            normalized["tz"] = normalized.get("timezone")
            normalized.pop("timezone", None)
        return normalized

    if kind == "at":
        if normalized.get("at") is None and normalized.get("time") is not None:
            normalized["at"] = normalized.get("time")
            normalized.pop("time", None)
        has_at = normalized.get("at") not in (None, "")
        has_after = normalized.get("after_seconds") is not None
        if has_at == has_after:
            raise ValueError("at schedule requires exactly one of at or after_seconds")
        if has_after:
            delay_seconds = normalized["after_seconds"]
            if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, int):
                raise ValueError("after_seconds must be a positive integer")
            if delay_seconds <= 0:
                raise ValueError("after_seconds must be greater than 0")
            normalized["at"] = to_iso_utc(utc_now() + timedelta(seconds=delay_seconds))
            normalized.pop("after_seconds")
        return normalized

    if kind != "every" or normalized.get("every_ms") is not None:
        return normalized

    for key, unit_alias in _EVERY_SCHEDULE_ALIASES:
        raw_value = normalized.get(key)
        if raw_value is None:
            continue
        value = int(raw_value or 0)
        if value <= 0:
            raise ValueError(f"{key} must be greater than 0")
        unit_value = normalized.get("unit") if unit_alias is None else unit_alias
        normalized["every_ms"] = value * _every_unit_multiplier(unit_value)
        for drop_key in _EVERY_SCHEDULE_ALIAS_KEYS:
            normalized.pop(drop_key, None)
        normalized.pop("unit", None)
        return normalized

    return normalized


def _context_metadata(ctx: RuntimeContext) -> Mapping[str, Any]:
    metadata = ctx.policy.raw.get("context_metadata")
    if not isinstance(metadata, Mapping):
        return {}
    return metadata


def _background_write_authorization_allowed(ctx: RuntimeContext) -> bool:
    if ctx.confirm:
        return True
    return _truthy_flag(
        _context_metadata(ctx).get("allow_background_write_authorization")
    )


def _origin_delivery_context(ctx: RuntimeContext) -> dict[str, str]:
    metadata = _context_metadata(ctx)
    origin: dict[str, str] = {}
    orchestration = metadata.get("orchestration")
    if isinstance(orchestration, Mapping):
        runtime_session_id = _safe_str(orchestration, "runtime_session_id")
        if runtime_session_id:
            origin["session_id"] = runtime_session_id
    for key in (
        "session_id",
        "channel",
        "target",
        "conversation_id",
        "thread_id",
        "attach_id",
    ):
        token = _safe_str(metadata, key)
        if token:
            origin[key] = token
    session_id = str(ctx.session_id or "").strip()
    if session_id:
        origin.setdefault("session_id", session_id)
    return origin


def _watch_delivery_payload(mode: str, origin: Mapping[str, str]) -> dict[str, Any]:
    normalized_mode = (mode or "announce").strip().lower() or "announce"
    if normalized_mode == "announce":
        return {
            "mode": "announce",
            "channel": "last",
            "to": "last",
        }
    if normalized_mode == "webhook":
        target = _safe_str(origin, "target")
        return {"mode": "webhook", "to": target}
    return {"mode": "none"}


def _nested_payload(
    payload: Mapping[str, Any] | None, key: str
) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get(key)
    if not isinstance(raw, Mapping):
        return None
    return dict(raw)


def _watch_payload(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return _nested_payload(payload, WATCH_PAYLOAD_KEY)


def _watch_metadata_from_payload(
    payload: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    watch = _watch_payload(payload)
    if watch is None:
        return None
    return {
        "description": _safe_str(watch, "description"),
        "check_instruction": _safe_str(watch, "check_instruction"),
        "alert_condition": _safe_str(watch, "alert_condition"),
        "on_condition_action": _safe_str(watch, "on_condition_action"),
        "delivery": _safe_str(watch, "delivery", "announce"),
        "check_profile_id": _safe_str(watch, "check_profile_id") or None,
        "target_id": _safe_str(watch, "target_id") or None,
        "stop_on_condition": bool(watch.get("stop_on_condition", True)),
        "delivery_cooldown_minutes": int(
            watch.get("delivery_cooldown_minutes", 0) or 0
        ),
        "deliver_resolution": bool(watch.get("deliver_resolution", False)),
        "max_checks": int(
            watch.get("max_checks", DEFAULT_WATCH_MAX_CHECKS)
            or DEFAULT_WATCH_MAX_CHECKS
        ),
        "checks_completed": int(watch.get("checks_completed", 0) or 0),
        "ttl_minutes": int(
            watch.get("ttl_minutes", DEFAULT_WATCH_TTL_MINUTES)
            or DEFAULT_WATCH_TTL_MINUTES
        ),
        "timeout_seconds": int(
            watch.get("timeout_seconds", DEFAULT_WATCH_TIMEOUT_SECONDS)
            or DEFAULT_WATCH_TIMEOUT_SECONDS
        ),
        "write_authorized": bool(watch.get("write_authorized", False)),
        "write_audit": list(watch.get("write_audit", []) or []),
        "last_check_at": watch.get("last_check_at"),
        "last_check_summary": watch.get("last_check_summary"),
        "last_condition_met": bool(watch.get("last_condition_met", False)),
        "last_alert_requested_at": watch.get("last_alert_requested_at"),
        "last_terminal_reason": _safe_str(watch, "last_terminal_reason"),
    }


def _consolidation_metadata_from_payload(
    payload: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    consolidation = _nested_payload(payload, CONSOLIDATION_PAYLOAD_KEY)
    if consolidation is None:
        return None
    return {
        "batch_limit": int(
            consolidation.get(
                "batch_limit",
                DEFAULT_CONSOLIDATION_BATCH_LIMIT,
            )
            or DEFAULT_CONSOLIDATION_BATCH_LIMIT
        ),
        "target_scope": _safe_str(consolidation, "target_scope"),
        "timeout_seconds": int(
            consolidation.get(
                "timeout_seconds",
                DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS,
            )
            or DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS
        ),
        "max_iterations": int(
            consolidation.get(
                "max_iterations",
                DEFAULT_CONSOLIDATION_MAX_ITERATIONS,
            )
            or DEFAULT_CONSOLIDATION_MAX_ITERATIONS
        ),
    }


__all__ = [
    "_background_write_authorization_allowed",
    "_coerce_schedule_aliases",
    "_consolidation_metadata_from_payload",
    "_context_metadata",
    "_origin_delivery_context",
    "_safe_str",
    "_text",
    "_watch_delivery_payload",
    "_watch_metadata_from_payload",
]
