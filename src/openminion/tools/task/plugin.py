from contextlib import contextmanager
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from openminion.modules.brain.runtime.goal.policy import authorize_goal_action
from openminion.modules.task.scheduling.schedule import (
    normalize_schedule,
    parse_iso_datetime,
    utc_now,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_TASK_CONSOLIDATE_MEMORY,
    MODEL_TASK_CANCEL,
    MODEL_TASK_LIST,
    MODEL_TASK_PAUSE,
    MODEL_TASK_RESUME,
    MODEL_TASK_SCHEDULE,
    MODEL_TASK_SHOW,
    MODEL_TASK_WATCH,
)
from openminion.modules.tool.runtime.environment import (
    agent_id_from_context as _agent_id_from_context,
)
from openminion.modules.tool.runtime.context import resolve_cron_repository
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.task import TaskManager
from openminion.modules.task.constants import (
    DEFAULT_TASK_MIN_EVERY_MS,
    TASK_INTERNAL_PAUSE_REASON_KEY,
    TASK_INTERNAL_PAUSE_SOURCE_KEY,
    TASK_REASON_RESUME_EXPIRED_ONE_SHOT,
    TASK_REASON_SCHEDULE_INTERVAL_TOO_SHORT,
)

from .constants import (
    CONSOLIDATION_PAYLOAD_KEY,
    DEFAULT_TASK_NAME_MAX_CHARS,
    DEFAULT_CONSOLIDATION_MAX_ITERATIONS,
    DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS,
    DEFAULT_WATCH_MAX_ITERATIONS,
    EVERY_UNIT_TO_MS,
    TASK_REASON_RECORD_NOT_FOUND,
    TASK_REASON_STORAGE_EXEC_ERROR,
    TASK_REASON_STORAGE_UNAVAILABLE,
    TASK_REASON_STORAGE_UNCONFIGURED,
    WATCH_DEFAULT_ALLOWED_TOOLS,
    WATCH_PAYLOAD_KEY,
    WATCH_TURN_KIND_CHECK,
)
from .args import (
    TaskCancelArgs,
    TaskConsolidateMemoryArgs,
    TaskListArgs,
    TaskPauseArgs,
    TaskResumeArgs,
    TaskScheduleArgs,
    TaskShowArgs,
    TaskWatchArgs,
)
from .scheduled_task.runtime import (
    _background_write_authorization_allowed,
    _context_metadata,
    _find_existing_scheduled_task,
    _origin_delivery_context,
    _safe_str,
    _text,
    _watch_delivery_payload,
    _watch_payload,
)
from .scheduled_task.views import (
    _latest_run_fields,
    _task_list_payload,
    _task_show_payload,
)


def _tool_error(
    code: str,
    *,
    message: str,
    reason_code: str,
    details: Mapping[str, Any] | None = None,
) -> ToolRuntimeError:
    payload = dict(details or {})
    payload["reason_code"] = str(reason_code)
    return ToolRuntimeError(code, message, payload)


def _enforce_goal_execution_policy(
    *,
    ctx: RuntimeContext,
    goal_origin_action_type: str | None,
    surface: str,
) -> None:
    normalized_action_type = _normalized_goal_origin_action_type(
        ctx=ctx,
        goal_origin_action_type=goal_origin_action_type,
        surface=surface,
    )
    if not normalized_action_type:
        return
    profile = getattr(ctx, "agent_profile", None)
    if profile is None:
        return
    policy = getattr(profile, "goal_execution_policy", None) or "suggest"
    auth = authorize_goal_action(
        profile_policy=policy,
        action_type=normalized_action_type,
    )
    if auth.allowed:
        return
    raise _tool_error(
        "POLICY_DENIED",
        message=(
            f"goal_execution_policy={policy!r} does not permit "
            f"auto-creating a {surface} for action_type="
            f"{normalized_action_type!r}. Surface this as a "
            "suggestion to the user before creating the action."
        ),
        reason_code=auth.reason,
        details={
            "field": "goal_origin_action_type",
            "surface": surface,
            "policy": str(policy),
            "action_type": str(normalized_action_type),
            "requires_user_confirm": bool(auth.requires_user_confirm),
            "risk_tier": auth.risk_tier,
        },
    )


def _goal_backed_context_present(ctx: RuntimeContext) -> bool:
    metadata = _context_metadata(ctx)
    if _truthy(metadata.get("goal_backed_request")):
        return True
    if _truthy(metadata.get("goal_origin_enforced")):
        return True
    refs = metadata.get("decision_memory_refs")
    if isinstance(refs, Mapping):
        return bool(refs)
    if isinstance(refs, (list, tuple, set)):
        return bool(refs)
    return False


def _normalized_goal_origin_action_type(
    *,
    ctx: RuntimeContext,
    goal_origin_action_type: str | None,
    surface: str,
) -> str | None:
    token = _text(goal_origin_action_type).lower()
    if not token or token == "none":
        return None
    if token != surface:
        return token
    if _goal_backed_context_present(ctx):
        return token
    # Some providers echo the optional goal-origin marker on direct user
    # task/watch calls. Treat same-surface markers as advisory unless the
    # runtime explicitly marked this turn as goal-backed.
    return None


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


def _resolve_cron_store(ctx: RuntimeContext) -> Any:
    repository = resolve_cron_repository(ctx)
    if repository is not None:
        return repository
    cron_path = getattr(ctx.repositories, "cron_db_path", None)
    if cron_path is None:
        raise _tool_error(
            "DEPENDENCY_MISSING",
            message="Cron storage is not configured",
            reason_code=TASK_REASON_STORAGE_UNCONFIGURED,
        )
    raise _tool_error(
        "DEPENDENCY_MISSING",
        message="Cron storage is unavailable",
        reason_code=TASK_REASON_STORAGE_UNAVAILABLE,
        details={"cron_db_path": str(cron_path)},
    )


def _resolve_task_manager(ctx: RuntimeContext) -> TaskManager:
    store = _resolve_cron_store(ctx)
    try:
        return TaskManager.from_cron_repository(
            store,
            db_path=getattr(store, "db_path", None),
        )
    except ToolRuntimeError:
        raise
    except Exception as exc:
        raise _tool_error(
            "EXEC_ERROR",
            message="Failed to initialize task lifecycle manager",
            reason_code=TASK_REASON_STORAGE_EXEC_ERROR,
            details={"reason": str(exc)},
        ) from exc


@contextmanager
def _storage_operation(
    *,
    message: str,
    details: Mapping[str, Any] | None = None,
    passthrough: tuple[type[BaseException], ...] = (),
):
    try:
        yield
    except passthrough:
        raise
    except ToolRuntimeError:
        raise
    except Exception as exc:
        payload = dict(details or {})
        payload["reason"] = str(exc)
        raise _tool_error(
            "EXEC_ERROR",
            message=message,
            reason_code=TASK_REASON_STORAGE_EXEC_ERROR,
            details=payload,
        ) from exc


def _derive_task_name(*, name: str | None, instruction: str) -> str:
    if name:
        return name
    compact = " ".join(instruction.split())
    if len(compact) <= DEFAULT_TASK_NAME_MAX_CHARS:
        return compact
    return f"{compact[: DEFAULT_TASK_NAME_MAX_CHARS - 3].rstrip()}..."


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
        return normalized

    if kind != "every":
        return normalized

    if normalized.get("every_ms") is not None:
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


def _enforce_every_schedule_floor(schedule: Mapping[str, Any]) -> None:
    if _safe_str(schedule, "kind") != "every":
        return
    every_ms = int(schedule.get("every_ms", 0) or 0)
    if every_ms >= DEFAULT_TASK_MIN_EVERY_MS:
        return
    raise _tool_error(
        "INVALID_ARGUMENT",
        message=(
            "Recurring task cadence is below the minimum allowed interval of "
            f"{DEFAULT_TASK_MIN_EVERY_MS} ms"
        ),
        reason_code=TASK_REASON_SCHEDULE_INTERVAL_TOO_SHORT,
        details={
            "field": "schedule.every_ms",
            "every_ms": every_ms,
            "minimum_every_ms": DEFAULT_TASK_MIN_EVERY_MS,
        },
    )


def _paused_reason_from_payload(payload: Mapping[str, Any] | None) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    token = _text(payload.get(TASK_INTERNAL_PAUSE_REASON_KEY))
    return token or None


def _apply_pause_metadata(
    payload: Mapping[str, Any] | None,
    *,
    reason: str | None,
    source: str | None,
) -> dict[str, Any]:
    updated = dict(payload or {})
    if reason:
        updated[TASK_INTERNAL_PAUSE_REASON_KEY] = str(reason)
        updated[TASK_INTERNAL_PAUSE_SOURCE_KEY] = str(source or "operator")
    else:
        updated.pop(TASK_INTERNAL_PAUSE_REASON_KEY, None)
        updated.pop(TASK_INTERNAL_PAUSE_SOURCE_KEY, None)
    return updated


def _owned_job_or_error(
    *,
    manager: TaskManager,
    task_id: str,
    caller_agent_id: str | None,
) -> dict[str, Any]:
    with _storage_operation(
        message="Failed to resolve scheduled task",
        details={"task_id": task_id},
    ):
        job = manager.get_scheduled_job(task_id)
    if job is None:
        raise _tool_error(
            "NOT_FOUND",
            message="Scheduled task not found",
            reason_code=TASK_REASON_RECORD_NOT_FOUND,
            details={"task_id": task_id},
        )
    owner = _safe_str(job, "agent_id")
    if owner and owner != caller_agent_id:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "Scheduled task is owned by another agent",
            {"task_id": task_id},
        )
    return job


def _h_task_schedule(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = TaskScheduleArgs.model_validate(args)
    # same gate as the watch surface — when the model
    _enforce_goal_execution_policy(
        ctx=ctx,
        goal_origin_action_type=validated.goal_origin_action_type,
        surface="task",
    )
    instruction = validated.instruction
    raw_schedule = _coerce_schedule_aliases(validated.schedule or {})
    task_name = _derive_task_name(name=validated.name, instruction=instruction)

    try:
        normalized_schedule = normalize_schedule(raw_schedule)
    except Exception as exc:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"Invalid schedule: {exc}",
            {"field": "schedule"},
        ) from exc
    _enforce_every_schedule_floor(normalized_schedule)

    schedule_kind = _safe_str(normalized_schedule, "kind")
    delete_after_run = schedule_kind == "at"
    payload: dict[str, Any] = {
        "kind": "agentTurn",
        "message": instruction,
    }
    origin = _origin_delivery_context(ctx)
    if origin:
        payload["_openminion_origin"] = origin
    agent_id = _agent_id_from_context(ctx)
    manager = _resolve_task_manager(ctx)
    with _storage_operation(message="Failed to create scheduled task"):
        existing_job = _find_existing_scheduled_task(
            manager,
            name=task_name,
            schedule=normalized_schedule,
            payload=payload,
            agent_id=agent_id,
            session_target="isolated",
            delete_after_run=delete_after_run,
        )
        if existing_job is None:
            task_row = manager.schedule_task(
                name=task_name,
                schedule=normalized_schedule,
                payload=payload,
                agent_id=agent_id,
                session_target="isolated",
                delete_after_run=delete_after_run,
                misfire_policy="skip",
            )
            task_id = task_row.task_id
            job = manager.get_scheduled_job(task_id) or {}
            deduped = False
        else:
            task_id = _safe_str(existing_job, "job_id")
            job = existing_job
            deduped = True

    return {
        "ok": True,
        "task_id": task_id,
        "deduped": deduped,
        "name": _safe_str(job, "name", task_name),
        "enabled": bool(job.get("enabled", True)),
        "schedule": dict(job.get("schedule") or normalized_schedule),
        "session_target": _safe_str(job, "session_target", "isolated"),
        "next_due_at": job.get("next_due_at"),
        "delete_after_run": bool(job.get("delete_after_run", delete_after_run)),
        "scheduler_note": (
            "Task scheduled. Runs will only execute while the openminion daemon is running. "
            "Start it with: openminion daemon start"
        ),
    }


def _h_task_watch(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = TaskWatchArgs.model_validate(args)
    # when the model marks this watch as backing a recalled
    _enforce_goal_execution_policy(
        ctx=ctx,
        goal_origin_action_type=validated.goal_origin_action_type,
        surface="watch",
    )
    write_authorized = bool(validated.write_authorized)
    if write_authorized and not _background_write_authorization_allowed(ctx):
        raise _tool_error(
            "POLICY_DENIED",
            message=(
                "Background write authorization is disabled for this agent. "
                "Enable runtime.allow_background_write_authorization or retry "
                "with an explicit confirmation context."
            ),
            reason_code="background_write_authorization_disabled",
            details={"field": "write_authorized"},
        )
    manager = _resolve_task_manager(ctx)
    agent_id = _agent_id_from_context(ctx)
    origin = _origin_delivery_context(ctx)
    job_id = uuid4().hex
    watch_session_id = f"watch:{job_id}"
    interval_minutes = int(validated.interval_minutes)
    timeout_seconds = int(validated.timeout_seconds)
    watch_metadata: dict[str, Any] = {
        "description": validated.description,
        "check_instruction": validated.check_instruction,
        "alert_condition": validated.alert_condition,
        "on_condition_action": validated.on_condition_action,
        "delivery": validated.delivery,
        "interval_minutes": interval_minutes,
        "max_checks": int(validated.max_checks),
        "checks_completed": 0,
        "ttl_minutes": int(validated.ttl_minutes),
        "timeout_seconds": timeout_seconds,
        "max_iterations": DEFAULT_WATCH_MAX_ITERATIONS,
        "allowed_tools": list(WATCH_DEFAULT_ALLOWED_TOOLS),
        "turn_kind": WATCH_TURN_KIND_CHECK,
        "write_authorized": write_authorized,
        "write_audit": [],
        "created_at": None,
        "last_check_at": None,
        "last_check_summary": None,
        "last_condition_met": False,
        "last_terminal_reason": "",
    }
    # persist the typed routine payload under
    if validated.routine is not None:
        watch_metadata["routine"] = validated.routine.model_dump(mode="json")
    payload: dict[str, Any] = {
        "kind": "agentTurn",
        "message": validated.check_instruction,
        "session_id": watch_session_id,
        WATCH_PAYLOAD_KEY: watch_metadata,
    }
    if origin:
        payload["_openminion_origin"] = origin
    schedule = {"kind": "every", "every_ms": interval_minutes * 60_000}
    with _storage_operation(message="Failed to create watch subscription"):
        task_row = manager.schedule_task(
            name=_derive_task_name(
                name=validated.description, instruction=validated.description
            ),
            schedule=schedule,
            payload=payload,
            description=validated.description,
            agent_id=agent_id,
            session_target="isolated",
            delivery=_watch_delivery_payload(validated.delivery, origin),
            delete_after_run=False,
            misfire_policy="skip",
            max_concurrency=1,
            job_id=job_id,
        )
        job = manager.get_scheduled_job(task_row.task_id) or {}
        stored_payload = dict(job.get("payload") or payload)
        watch_payload = _watch_payload(stored_payload) or {}
        watch_payload["created_at"] = job.get("created_at")
        stored_payload[WATCH_PAYLOAD_KEY] = watch_payload
        manager.replace_cron_job_payload(task_row.task_id, stored_payload)
        job = manager.get_scheduled_job(task_row.task_id) or job

    return {
        "ok": True,
        "task_id": task_row.task_id,
        "watch_created": True,
        "name": _safe_str(job, "name", validated.description),
        "enabled": bool(job.get("enabled", True)),
        "schedule": dict(job.get("schedule") or schedule),
        "delivery": validated.delivery,
        "on_condition_action": validated.on_condition_action,
        "write_authorized": write_authorized,
        "max_checks": int(validated.max_checks),
        "checks_completed": 0,
        "watch_session_id": watch_session_id,
        "next_due_at": job.get("next_due_at"),
        "scheduler_note": (
            "Watch scheduled. Checks run only while the openminion daemon is running. "
            "Start it with: openminion daemon start"
        ),
    }


def _h_task_consolidate_memory(
    args: dict[str, Any], ctx: RuntimeContext
) -> dict[str, Any]:
    validated = TaskConsolidateMemoryArgs.model_validate(args)
    manager = _resolve_task_manager(ctx)
    agent_id = _agent_id_from_context(ctx)
    interval_hours = int(validated.interval_hours)
    batch_limit = int(validated.batch_limit)
    job_id = uuid4().hex
    session_id = f"consolidate:{job_id}"
    target_scope = f"agent:{agent_id}"
    payload: dict[str, Any] = {
        "kind": "agentTurn",
        "message": (
            "Review the provided memory candidates, decide which to promote, "
            "discard, or defer, then provide a brief summary and the "
            "memory_consolidation trailer."
        ),
        "session_id": session_id,
        CONSOLIDATION_PAYLOAD_KEY: {
            "batch_limit": batch_limit,
            "max_iterations": DEFAULT_CONSOLIDATION_MAX_ITERATIONS,
            "timeout_seconds": DEFAULT_CONSOLIDATION_TIMEOUT_SECONDS,
            "target_scope": target_scope,
        },
    }
    schedule = {"kind": "every", "every_ms": interval_hours * 3_600_000}
    with _storage_operation(message="Failed to create memory consolidation task"):
        task_row = manager.schedule_task(
            name=_derive_task_name(
                name=validated.name,
                instruction="memory consolidation",
            ),
            schedule=schedule,
            payload=payload,
            description="Recurring memory consolidation",
            agent_id=agent_id,
            session_target="isolated",
            delivery={"mode": "none"},
            delete_after_run=False,
            misfire_policy="skip",
            max_concurrency=1,
            job_id=job_id,
        )
        job = manager.get_scheduled_job(task_row.task_id) or {}

    return {
        "ok": True,
        "task_id": task_row.task_id,
        "name": _safe_str(job, "name", "memory consolidation"),
        "enabled": bool(job.get("enabled", True)),
        "schedule": dict(job.get("schedule") or schedule),
        "batch_limit": batch_limit,
        "target_scope": target_scope,
        "consolidation_session_id": session_id,
        "next_due_at": job.get("next_due_at"),
        "scheduler_note": (
            "Memory consolidation scheduled. Runs execute only while the openminion daemon "
            "is running. Start it with: openminion daemon start"
        ),
    }


def _h_task_cancel(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = TaskCancelArgs.model_validate(args)
    task_id = validated.task_id
    caller_agent_id = _agent_id_from_context(ctx)
    manager = _resolve_task_manager(ctx)
    with _storage_operation(
        message="Failed to resolve scheduled task",
        details={"task_id": task_id},
    ):
        job = manager.get_scheduled_job(task_id)
    if job is None:
        raise _tool_error(
            "NOT_FOUND",
            message="Scheduled task not found",
            reason_code=TASK_REASON_RECORD_NOT_FOUND,
            details={"task_id": validated.task_id},
        )

    owner = _safe_str(job, "agent_id")
    if owner and owner != caller_agent_id:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "Scheduled task is owned by another agent",
            {"task_id": task_id},
        )

    with _storage_operation(
        message="Failed to cancel scheduled task",
        details={"task_id": task_id},
        passthrough=(KeyError,),
    ):
        manager.cancel_task(task_id)

    return {
        "ok": True,
        "task_id": task_id,
        "cancelled": True,
        "task_cancelled": True,
    }


def _h_task_pause(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = TaskPauseArgs.model_validate(args)
    task_id = validated.task_id
    caller_agent_id = _agent_id_from_context(ctx)
    manager = _resolve_task_manager(ctx)
    job = _owned_job_or_error(
        manager=manager,
        task_id=task_id,
        caller_agent_id=caller_agent_id,
    )
    updated_payload = _apply_pause_metadata(
        job.get("payload"),
        reason=None,
        source="operator",
    )
    with _storage_operation(
        message="Failed to pause scheduled task",
        details={"task_id": task_id},
        passthrough=(KeyError,),
    ):
        if updated_payload != dict(job.get("payload") or {}):
            manager.replace_cron_job_payload(task_id, updated_payload)
        _, paused_job = manager.pause_task(task_id)
    latest_run_state, _, _ = _latest_run_fields(manager, job_id=task_id)
    return {
        "ok": True,
        "task_id": task_id,
        "paused": True,
        "enabled": bool(paused_job.get("enabled", False)),
        "schedule": dict(paused_job.get("schedule") or {}),
        "next_due_at": paused_job.get("next_due_at"),
        "last_run_state": latest_run_state,
    }


def _h_task_resume(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = TaskResumeArgs.model_validate(args)
    task_id = validated.task_id
    caller_agent_id = _agent_id_from_context(ctx)
    manager = _resolve_task_manager(ctx)
    job = _owned_job_or_error(
        manager=manager,
        task_id=task_id,
        caller_agent_id=caller_agent_id,
    )
    schedule = dict(job.get("schedule") or {})
    if _safe_str(schedule, "kind") == "at":
        at_raw = _text(schedule.get("at"))
        if at_raw:
            try:
                if parse_iso_datetime(at_raw) <= utc_now():
                    raise _tool_error(
                        "INVALID_ARGUMENT",
                        message="One-shot task is already expired and cannot be resumed",
                        reason_code=TASK_REASON_RESUME_EXPIRED_ONE_SHOT,
                        details={"task_id": task_id},
                    )
            except ToolRuntimeError:
                raise
            except Exception:
                pass
    updated_payload = _apply_pause_metadata(
        job.get("payload"),
        reason=None,
        source=None,
    )
    with _storage_operation(
        message="Failed to resume scheduled task",
        details={"task_id": task_id},
        passthrough=(KeyError,),
    ):
        if updated_payload != dict(job.get("payload") or {}):
            manager.replace_cron_job_payload(task_id, updated_payload)
        _, resumed_job = manager.resume_task(task_id)
    return {
        "ok": True,
        "task_id": task_id,
        "resumed": True,
        "enabled": bool(resumed_job.get("enabled", False)),
        "schedule": dict(resumed_job.get("schedule") or {}),
        "next_due_at": resumed_job.get("next_due_at"),
    }


def _h_task_show(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = TaskShowArgs.model_validate(args)
    task_id = validated.task_id
    caller_agent_id = _agent_id_from_context(ctx)
    manager = _resolve_task_manager(ctx)
    job = _owned_job_or_error(
        manager=manager,
        task_id=task_id,
        caller_agent_id=caller_agent_id,
    )
    effective_runs_limit = max(1, min(int(validated.runs_limit), 20))
    return {
        "ok": True,
        "task": _task_show_payload(
            manager=manager,
            job=job,
            runs_limit=effective_runs_limit,
            pause_reason=_paused_reason_from_payload(job.get("payload")),
        ),
        "runs_limit": effective_runs_limit,
    }


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "on"}


def _task_list_policy(ctx: RuntimeContext) -> tuple[bool, bool]:
    allow_cross_agent = False
    include_unowned = False
    policy_raw = getattr(ctx.policy, "raw", {}) or {}
    if not isinstance(policy_raw, Mapping):
        return allow_cross_agent, include_unowned

    include_unowned = _truthy(policy_raw.get("task_list_include_unowned"))
    allow_cross_agent = _truthy(policy_raw.get("task_list_allow_cross_agent"))

    context_meta = policy_raw.get("context_metadata")
    if isinstance(context_meta, Mapping):
        include_unowned = include_unowned or _truthy(
            context_meta.get("task_list_include_unowned")
        )
        allow_cross_agent = allow_cross_agent or _truthy(
            context_meta.get("task_list_allow_cross_agent")
        )
    return allow_cross_agent, include_unowned


def _h_task_list(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    validated = TaskListArgs.model_validate(args)
    requested_limit = int(validated.limit)
    effective_limit = max(1, min(requested_limit, 100))
    expanded_limit = max(effective_limit * 4, 100)

    caller_agent_id = _agent_id_from_context(ctx)
    allow_cross_agent, include_unowned = _task_list_policy(ctx)
    manager = _resolve_task_manager(ctx)
    with _storage_operation(message="Failed to list scheduled tasks"):
        jobs = manager.list_scheduled_jobs(limit=expanded_limit)

    visible_jobs: list[dict[str, Any]] = []
    for job in jobs:
        try:
            manager.ensure_task_record_for_job(job)
        except Exception:
            # Lifecycle backfill is best-effort for pre-TaskManager rows.
            pass
        owner = _safe_str(job, "agent_id")
        if owner:
            if not allow_cross_agent and owner != caller_agent_id:
                continue
        elif not include_unowned:
            continue
        visible_jobs.append(dict(job))

    tasks = _task_list_payload(
        manager=manager,
        jobs=visible_jobs,
        effective_limit=effective_limit,
    )

    return {
        "ok": True,
        "tasks": tasks,
        "count": len(tasks),
        "limit": effective_limit,
    }


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name=MODEL_TASK_SCHEDULE,
            args_model=TaskScheduleArgs,
            min_scope="WRITE_SAFE",
            handler=_h_task_schedule,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "task", "schedule"),
            capabilities=("task", "schedule"),
            block_under_readonly=True,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_TASK_CONSOLIDATE_MEMORY,
            args_model=TaskConsolidateMemoryArgs,
            min_scope="WRITE_SAFE",
            handler=_h_task_consolidate_memory,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "task", "memory"),
            capabilities=("task", "schedule"),
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_TASK_CANCEL,
            args_model=TaskCancelArgs,
            min_scope="WRITE_SAFE",
            handler=_h_task_cancel,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "task", "schedule"),
            capabilities=("task", "schedule"),
            block_under_readonly=True,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_TASK_WATCH,
            args_model=TaskWatchArgs,
            min_scope="WRITE_SAFE",
            handler=_h_task_watch,
            dangerous=False,
            idempotent=False,
            tags=("plugin", "task", "watch"),
            capabilities=("task", "schedule"),
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_TASK_LIST,
            args_model=TaskListArgs,
            min_scope="READ_ONLY",
            handler=_h_task_list,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "task", "schedule"),
            capabilities=("task", "schedule"),
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_TASK_PAUSE,
            args_model=TaskPauseArgs,
            min_scope="WRITE_SAFE",
            handler=_h_task_pause,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "task", "schedule"),
            capabilities=("task", "schedule"),
            block_under_readonly=True,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_TASK_RESUME,
            args_model=TaskResumeArgs,
            min_scope="WRITE_SAFE",
            handler=_h_task_resume,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "task", "schedule"),
            capabilities=("task", "schedule"),
            block_under_readonly=True,
        )
    )
    registry.add(
        ToolSpec(
            name=MODEL_TASK_SHOW,
            args_model=TaskShowArgs,
            min_scope="READ_ONLY",
            handler=_h_task_show,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "task", "schedule"),
            capabilities=("task", "schedule"),
        )
    )


__all__ = [
    "TaskCancelArgs",
    "TaskConsolidateMemoryArgs",
    "TaskListArgs",
    "TaskPauseArgs",
    "TaskResumeArgs",
    "TaskScheduleArgs",
    "TaskShowArgs",
    "TaskWatchArgs",
    "_h_task_cancel",
    "_h_task_consolidate_memory",
    "_h_task_list",
    "_h_task_pause",
    "_h_task_resume",
    "_h_task_watch",
    "_h_task_show",
    "_resolve_cron_store",
    "_h_task_schedule",
    "register",
]
