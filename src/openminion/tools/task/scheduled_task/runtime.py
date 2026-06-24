"""Scheduled-task runtime helpers."""

from collections.abc import Mapping
from typing import Any

from openminion.modules.task import TaskManager
from openminion.modules.tool.runtime.context import RuntimeContext
from openminion.services.cron.scheduling import normalize_payload, normalize_schedule

from ..constants import (
    CONSOLIDATION_PAYLOAD_KEY,
    DEFAULT_CONSOLIDATION_BATCH_LIMIT,
    DEFAULT_CONSOLIDATION_MAX_ITERATIONS,
    DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS,
    DEFAULT_WATCH_MAX_CHECKS,
    DEFAULT_WATCH_TIMEOUT_SECONDS,
    DEFAULT_WATCH_TTL_MINUTES,
    WATCH_PAYLOAD_KEY,
)


def _safe_str(obj: Mapping[str, Any], key: str, default: str = "") -> str:
    return str(obj.get(key, default) or default).strip()


def _text(value: Any) -> str:
    return str(value).strip() if value else ""


def _truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _context_metadata(ctx: RuntimeContext) -> Mapping[str, Any]:
    raw = getattr(getattr(ctx, "policy", None), "raw", {}) or {}
    if not isinstance(raw, Mapping):
        return {}
    metadata = raw.get("context_metadata")
    if not isinstance(metadata, Mapping):
        return {}
    return metadata


def _background_write_authorization_allowed(ctx: RuntimeContext) -> bool:
    if bool(getattr(ctx, "confirm", False)):
        return True
    return _truthy_flag(
        _context_metadata(ctx).get("allow_background_write_authorization")
    )


def _origin_delivery_context(ctx: RuntimeContext) -> dict[str, str]:
    metadata = _context_metadata(ctx)
    origin: dict[str, str] = {}
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
    return origin


def _watch_delivery_payload(mode: str, origin: Mapping[str, str]) -> dict[str, Any]:
    normalized_mode = str(mode or "announce").strip().lower() or "announce"
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


def _watch_payload(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get(WATCH_PAYLOAD_KEY)
    if not isinstance(raw, Mapping):
        return None
    return dict(raw)


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
        "last_terminal_reason": _safe_str(watch, "last_terminal_reason"),
    }


def _consolidation_payload(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get(CONSOLIDATION_PAYLOAD_KEY)
    if not isinstance(raw, Mapping):
        return None
    return dict(raw)


def _consolidation_metadata_from_payload(
    payload: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    consolidation = _consolidation_payload(payload)
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


def _find_existing_scheduled_task(
    manager: TaskManager,
    *,
    name: str,
    schedule: Mapping[str, Any],
    payload: Mapping[str, Any],
    agent_id: str,
    session_target: str,
    delete_after_run: bool,
) -> dict[str, Any] | None:
    normalized_payload = normalize_payload(payload)
    for job in manager.list_scheduled_jobs(limit=1000):
        if not bool(job.get("enabled", False)):
            continue
        if _safe_str(job, "name") != name:
            continue
        if _safe_str(job, "agent_id") != agent_id:
            continue
        if _safe_str(job, "session_target") != session_target:
            continue
        if bool(job.get("delete_after_run", False)) != bool(delete_after_run):
            continue
        try:
            job_schedule = normalize_schedule(job.get("schedule") or {})
            job_payload = normalize_payload(job.get("payload") or {})
        except Exception:
            continue
        if job_schedule == dict(schedule) and job_payload == normalized_payload:
            return dict(job)
    return None


__all__ = [
    "_background_write_authorization_allowed",
    "_consolidation_metadata_from_payload",
    "_context_metadata",
    "_find_existing_scheduled_task",
    "_origin_delivery_context",
    "_safe_str",
    "_text",
    "_watch_delivery_payload",
    "_watch_metadata_from_payload",
]
