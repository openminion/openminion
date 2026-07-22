from collections.abc import Callable
from typing import Any

from openminion.tools.task.constants import (
    DEFAULT_CONSOLIDATION_BATCH_LIMIT,
    DEFAULT_CONSOLIDATION_MAX_ITERATIONS,
    DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS,
    DEFAULT_WATCH_MAX_CHECKS,
    DEFAULT_WATCH_MAX_ITERATIONS,
    DEFAULT_WATCH_TIMEOUT_SECONDS,
    WATCH_DEFAULT_ALLOWED_TOOLS,
    WATCH_TURN_KIND_ACTION,
    WATCH_TURN_KIND_CHECK,
)

MetadataResolver = Callable[[dict[str, Any]], dict[str, Any] | None]

WatchOutputBuilder = Callable[..., dict[str, Any]]
WatchTerminalSummaryBuilder = Callable[..., str]
WatchTtlChecker = Callable[..., bool]


def build_expired_watch_result(
    *,
    watch_output: WatchOutputBuilder,
    watch_terminal_summary: WatchTerminalSummaryBuilder,
    watch: dict[str, Any],
    checks_completed: int,
) -> dict[str, Any]:
    summary = watch_terminal_summary(
        watch=watch,
        checks_completed=checks_completed,
        terminal_reason="ttl_expired",
        fallback="Watch expired before the next check ran.",
    )
    return {
        "summary": summary,
        "output": watch_output(
            condition_met=False,
            terminal=True,
            deliver=True,
            checks_completed=checks_completed,
            terminal_reason="ttl_expired",
            summary=summary,
        ),
    }


def watch_condition_met(metadata: dict[str, Any]) -> bool:
    return str(metadata.get("watch_condition_met", "") or "").strip().lower() == "true"


def watch_terminal_state(
    *,
    watch_terminal_summary: WatchTerminalSummaryBuilder,
    watch_ttl_expired: WatchTtlChecker,
    job: dict[str, Any],
    watch: dict[str, Any],
    checks_completed: int,
    condition_met: bool,
    summary: str,
) -> dict[str, Any]:
    terminal_reason = ""
    deliver = bool(condition_met)
    terminal = bool(condition_met)
    if checks_completed >= _int_or_default(
        watch.get("max_checks"),
        DEFAULT_WATCH_MAX_CHECKS,
    ):
        terminal = True
        deliver = True
        terminal_reason = "max_checks_reached"
    elif watch_ttl_expired(job=job, watch=watch):
        terminal = True
        deliver = True
        terminal_reason = "ttl_expired"
    elif condition_met:
        terminal_reason = "condition_met"
    if terminal_reason in {"max_checks_reached", "ttl_expired"}:
        summary = watch_terminal_summary(
            watch=watch,
            checks_completed=checks_completed,
            terminal_reason=terminal_reason,
            fallback=summary,
        )
    return {
        "terminal": terminal,
        "deliver": deliver,
        "terminal_reason": terminal_reason,
        "summary": summary,
    }


def mark_idle_tick_request(request_payload: dict[str, Any], *, plan_id: str) -> None:
    for key in ("cron", "meta"):
        metadata = request_payload.setdefault(key, {})
        if not isinstance(metadata, dict):
            continue
        metadata["pae_idle_tick"] = "true"
        if plan_id:
            metadata["pae_plan_id"] = plan_id


def build_cron_request_payload(
    *,
    job: dict[str, Any],
    run: dict[str, Any],
    message: Any,
    payload: dict[str, Any] | None,
    consolidation_metadata: MetadataResolver,
    watch_metadata: MetadataResolver,
) -> dict[str, Any]:
    resolved_payload = _resolved_payload(job=job, payload=payload)
    request_payload: dict[str, Any] = {
        "message": message,
        "session_id": _request_session_id(payload=resolved_payload, run=run),
        "trace_id": run["run_id"],
    }
    cron_meta: dict[str, str] = {}
    _add_cron_run_meta(cron_meta=cron_meta, job=job, run=run)
    if isinstance(resolved_payload, dict):
        _add_payload_cron_meta(
            cron_meta,
            resolved_payload,
            consolidation_metadata=consolidation_metadata,
            watch_metadata=watch_metadata,
        )
    if cron_meta:
        request_payload["meta"] = cron_meta
    timeout_seconds = _request_timeout_seconds(resolved_payload, watch_metadata)
    if timeout_seconds > 0:
        request_payload["timeout_seconds"] = timeout_seconds
    return request_payload


def _resolved_payload(*, job: dict[str, Any], payload: dict[str, Any] | None) -> Any:
    return payload if isinstance(payload, dict) else job.get("payload", {})


def _request_session_id(*, payload: Any, run: dict[str, Any]) -> str:
    payload_session_id = ""
    if isinstance(payload, dict):
        payload_session_id = str(payload.get("session_id", "") or "").strip()
    isolated_session_id = str(run.get("isolated_session_id", "") or "").strip()
    return payload_session_id or isolated_session_id or "cron-session"


def _add_cron_run_meta(
    *,
    cron_meta: dict[str, str],
    job: dict[str, Any],
    run: dict[str, Any],
) -> None:
    for key, value in (
        ("cron_job_id", job.get("job_id", "")),
        ("cron_run_id", run.get("run_id", "")),
        ("scheduled_for", run.get("due_at", "") or job.get("next_due_at", "")),
    ):
        text = str(value or "").strip()
        if text:
            cron_meta[key] = text


def _add_payload_cron_meta(
    cron_meta: dict[str, str],
    payload: dict[str, Any],
    *,
    consolidation_metadata: MetadataResolver,
    watch_metadata: MetadataResolver,
) -> None:
    for key in ("linked_task_id", "goal_id", "mission_id"):
        value = str(payload.get(key, "") or "").strip()
        if value:
            cron_meta[key] = value
    _add_consolidation_cron_meta(cron_meta, consolidation_metadata(payload))
    watch = watch_metadata(payload)
    if watch is not None:
        _add_watch_cron_meta(cron_meta, watch)


def _add_consolidation_cron_meta(
    cron_meta: dict[str, str],
    consolidation: dict[str, Any] | None,
) -> None:
    if consolidation is None:
        return
    cron_meta["memory_consolidation_job"] = "true"
    cron_meta["memory_consolidation_target_scope"] = str(
        consolidation.get("target_scope", "") or ""
    ).strip()
    cron_meta["memory_consolidation_batch_limit"] = str(
        _int_or_default(consolidation.get("batch_limit"), DEFAULT_CONSOLIDATION_BATCH_LIMIT)
    )
    cron_meta["memory_consolidation_max_iterations"] = str(
        _int_or_default(
            consolidation.get("max_iterations"),
            DEFAULT_CONSOLIDATION_MAX_ITERATIONS,
        )
    )
    cron_meta["memory_consolidation_timeout_seconds"] = str(
        _int_or_default(
            consolidation.get("timeout_seconds"),
            DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS,
        )
    )


def _add_watch_cron_meta(cron_meta: dict[str, str], watch: dict[str, Any]) -> None:
    watch_turn_kind = (
        str(watch.get("turn_kind", WATCH_TURN_KIND_CHECK) or WATCH_TURN_KIND_CHECK).strip()
        or WATCH_TURN_KIND_CHECK
    )
    cron_meta["watch_job"] = "true"
    cron_meta["watch_turn_kind"] = watch_turn_kind
    cron_meta["watch_description"] = str(watch.get("description", "") or "").strip()
    cron_meta["watch_alert_condition"] = str(
        watch.get("alert_condition", "") or ""
    ).strip()
    if watch_turn_kind == WATCH_TURN_KIND_CHECK:
        cron_meta["watch_allowed_tools"] = _watch_allowed_tools_text(watch)
    if watch_turn_kind == WATCH_TURN_KIND_ACTION:
        cron_meta["watch_write_authorized"] = str(
            bool(watch.get("write_authorized", False))
        ).lower()
        cron_meta["watch_write_authorization_scope"] = "watch_job"
    cron_meta["watch_max_iterations"] = str(
        _int_or_default(watch.get("max_iterations"), DEFAULT_WATCH_MAX_ITERATIONS)
    )
    cron_meta["watch_timeout_seconds"] = str(
        _int_or_default(watch.get("timeout_seconds"), DEFAULT_WATCH_TIMEOUT_SECONDS)
    )


def _watch_allowed_tools_text(watch: dict[str, Any]) -> str:
    return ",".join(
        [
            str(item).strip()
            for item in list(
                watch.get("allowed_tools", WATCH_DEFAULT_ALLOWED_TOOLS)
                or WATCH_DEFAULT_ALLOWED_TOOLS
            )
            if str(item).strip()
        ]
    )


def _request_timeout_seconds(payload: Any, watch_metadata: MetadataResolver) -> int:
    if not isinstance(payload, dict):
        return 0
    timeout_seconds = int(payload.get("timeout_seconds", 0) or 0)
    if timeout_seconds > 0:
        return timeout_seconds
    watch = watch_metadata(payload)
    return int((watch or {}).get("timeout_seconds", 0) or 0)


def _int_or_default(value: Any, default: int) -> int:
    return int(value or default)
