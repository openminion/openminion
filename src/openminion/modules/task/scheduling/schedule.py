import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openminion.base.time import (
    utc_now as utc_now,
)  # re-exported by services/cron/__init__.py

from .constants import (
    ALLOWED_DELIVERY_MODES,
    ALLOWED_MISFIRE_MODES,
    ALLOWED_PAYLOAD_KINDS,
    ALLOWED_SCHEDULE_KINDS,
    ALLOWED_SESSION_TARGETS,
    ALLOWED_WAKE_MODES,
    DEFAULT_TOP_OF_HOUR_STAGGER_MS,
    PAYLOAD_KIND_AGENT_IDLE_TICK,
    PAYLOAD_KIND_SYSTEM_EVENT,
)

_CRON_FIELD_RE = re.compile(r"^(\*|\d+|\d+-\d+)(/\d+)?$")


def parse_iso_datetime(value: str) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("datetime value is required")
    normalized = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def normalize_wake_mode(value: str | None) -> str:
    mode = str(value or "now").strip() or "now"
    if mode not in ALLOWED_WAKE_MODES:
        raise ValueError(f"unsupported wake_mode: {mode}")
    return mode


def normalize_session_target(value: str | None) -> str:
    target = str(value or "").strip()
    if target not in ALLOWED_SESSION_TARGETS:
        raise ValueError(f"unsupported session_target: {target}")
    return target


def normalize_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    kind = str(payload.get("kind", "")).strip()
    if kind not in ALLOWED_PAYLOAD_KINDS:
        raise ValueError("payload.kind must be systemEvent or agentTurn")
    data = dict(payload)
    data["kind"] = kind
    if kind == PAYLOAD_KIND_SYSTEM_EVENT:
        text = str(data.get("event_text", "")).strip()
        if not text:
            raise ValueError("systemEvent payload requires event_text")
        data["event_text"] = text
        metadata = data.get("metadata")
        if metadata is not None and not isinstance(metadata, Mapping):
            raise ValueError("systemEvent metadata must be an object")
    elif kind == "agentTurn":
        message = str(data.get("message", "")).strip()
        if not message:
            raise ValueError("agentTurn payload requires message")
        data["message"] = message
        timeout = data.get("timeout_seconds")
        if timeout is not None:
            timeout_int = int(timeout)
            if timeout_int <= 0:
                raise ValueError("timeout_seconds must be greater than 0")
            data["timeout_seconds"] = timeout_int
    elif kind == PAYLOAD_KIND_AGENT_IDLE_TICK:
        # the idle tick fires on a specific existing session,
        session_id = str(data.get("session_id", "")).strip()
        if not session_id:
            raise ValueError("agentIdleTick payload requires session_id")
        data["session_id"] = session_id
        plan_id = str(data.get("plan_id", "")).strip()
        data["plan_id"] = plan_id  # may be empty; callers should pass it
        grace = data.get("user_activity_grace_seconds")
        if grace is not None:
            try:
                grace_int = max(0, int(grace))
            except (TypeError, ValueError):
                grace_int = 0
            data["user_activity_grace_seconds"] = grace_int
    return data


def normalize_delivery(delivery: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = delivery or {"mode": "none"}
    if not isinstance(raw, Mapping):
        raise ValueError("delivery must be an object")
    mode = str(raw.get("mode", "none")).strip() or "none"
    if mode not in ALLOWED_DELIVERY_MODES:
        raise ValueError(f"unsupported delivery.mode: {mode}")
    result: dict[str, Any] = {
        "mode": mode,
        "channel": str(raw.get("channel", "") or "").strip(),
        "to": str(raw.get("to", "") or "").strip(),
        "best_effort": bool(raw.get("best_effort", False)),
    }
    return result


def normalize_schedule(schedule: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(schedule, Mapping):
        raise ValueError("schedule must be an object")
    kind = str(schedule.get("kind", "")).strip()
    if kind not in ALLOWED_SCHEDULE_KINDS:
        raise ValueError("schedule.kind must be one of: at, every, cron")

    if kind == "at":
        at_dt = parse_iso_datetime(str(schedule.get("at", "")).strip())
        return {"kind": "at", "at": to_iso_utc(at_dt)}

    if kind == "every":
        every_ms = int(schedule.get("every_ms", 0))
        if every_ms <= 0:
            raise ValueError("every_ms must be greater than 0")
        jitter_ms = int(schedule.get("jitter_ms", 0) or 0)
        if jitter_ms < 0:
            raise ValueError("jitter_ms must be >= 0")
        return {"kind": "every", "every_ms": every_ms, "jitter_ms": jitter_ms}

    expr = str(schedule.get("expr", "")).strip()
    if not expr:
        raise ValueError("cron schedule requires expr")
    _parse_cron_expression(expr)
    tz_name = str(schedule.get("tz", "") or "").strip() or _default_timezone_name()
    _load_timezone(tz_name)
    if "stagger_ms" in schedule:
        stagger_raw = schedule.get("stagger_ms", 0)
    elif "staggerMs" in schedule:
        stagger_raw = schedule.get("staggerMs", 0)
    else:
        stagger_raw = (
            DEFAULT_TOP_OF_HOUR_STAGGER_MS if _is_top_of_hour_expression(expr) else 0
        )
    stagger_ms = int(stagger_raw or 0)
    if stagger_ms < 0:
        raise ValueError("stagger_ms must be >= 0")
    return {
        "kind": "cron",
        "expr": expr,
        "tz": tz_name,
        "stagger_ms": stagger_ms,
    }


def validate_target_payload_pair(*, session_target: str, payload_kind: str) -> None:
    if session_target == "main" and payload_kind != PAYLOAD_KIND_SYSTEM_EVENT:
        raise ValueError("session_target=main requires payload.kind=systemEvent")
    if session_target == "isolated" and payload_kind != "agentTurn":
        raise ValueError("session_target=isolated requires payload.kind=agentTurn")
    if (
        session_target == "agent_session"
        and payload_kind != PAYLOAD_KIND_AGENT_IDLE_TICK
    ):
        raise ValueError(
            "session_target=agent_session requires payload.kind=agentIdleTick"
        )
    # `agentIdleTick` is session-target-locked to `agent_session`
    if (
        payload_kind == PAYLOAD_KIND_AGENT_IDLE_TICK
        and session_target != "agent_session"
    ):
        raise ValueError(
            "payload.kind=agentIdleTick requires session_target=agent_session"
        )


def default_session_target_for_payload(payload_kind: str) -> str:
    if payload_kind == PAYLOAD_KIND_SYSTEM_EVENT:
        return "main"
    if payload_kind == PAYLOAD_KIND_AGENT_IDLE_TICK:
        return "agent_session"
    return "isolated"


def default_delete_after_run(schedule_kind: str) -> bool:
    return schedule_kind == "at"


@dataclass(frozen=True)
class MisfirePolicy:
    mode: str = "run_once"
    catch_up_max: int = 1


def normalize_misfire_policy(value: str | Mapping[str, Any] | None) -> MisfirePolicy:
    if value is None:
        return MisfirePolicy(mode="run_once", catch_up_max=1)

    if isinstance(value, Mapping):
        mode = str(value.get("mode", "run_once")).strip() or "run_once"
        catch_up_max = int(value.get("max", 1) or 1)
    else:
        raw = str(value).strip()
        if not raw:
            return MisfirePolicy(mode="run_once", catch_up_max=1)
        match = re.fullmatch(r"catch_up\(max=(\d+)\)", raw)
        if match:
            mode = "catch_up"
            catch_up_max = int(match.group(1))
        else:
            mode = raw
            catch_up_max = 1

    if mode not in ALLOWED_MISFIRE_MODES:
        raise ValueError("misfire_policy must be skip, run_once, or catch_up(max=N)")
    catch_up_max = max(1, int(catch_up_max))
    return MisfirePolicy(mode=mode, catch_up_max=catch_up_max)


def encode_misfire_policy(policy: MisfirePolicy) -> str:
    if policy.mode == "catch_up":
        return f"catch_up(max={max(1, int(policy.catch_up_max))})"
    return policy.mode


def compute_next_due(
    *,
    schedule: Mapping[str, Any],
    after: datetime,
    job_id: str,
    last_due: datetime | None = None,
) -> datetime | None:
    normalized = normalize_schedule(schedule)
    kind = normalized["kind"]
    if kind == "at":
        at_dt = parse_iso_datetime(str(normalized["at"]))
        return at_dt if at_dt > after else None

    if kind == "every":
        every_ms = int(normalized["every_ms"])
        base = (last_due or after).astimezone(timezone.utc)
        if last_due is None:
            base = after.astimezone(timezone.utc)
        next_due = base + timedelta(milliseconds=every_ms)
        jitter_ms = int(normalized.get("jitter_ms", 0) or 0)
        if jitter_ms > 0:
            next_due = next_due + timedelta(
                milliseconds=_deterministic_jitter_ms(job_id, next_due, jitter_ms)
            )
        return next_due

    expr = str(normalized["expr"])
    tz_name = str(normalized.get("tz", "UTC") or "UTC")
    due = _next_cron_occurrence(expr=expr, tz_name=tz_name, after=after)
    if due is None:
        return None
    stagger_ms = int(normalized.get("stagger_ms", 0) or 0)
    if stagger_ms <= 0:
        return due
    stagger = _deterministic_cron_stagger_ms(
        job_id=job_id, expr=expr, stagger_ms=stagger_ms
    )
    return due + timedelta(milliseconds=stagger)


def _select_due_points_for_job(
    *,
    job: Mapping[str, Any],
    now_dt: datetime,
) -> tuple[list[datetime], datetime | None]:
    schedule = normalize_schedule(job.get("schedule", {}))
    next_due_raw = str(job.get("next_due_at") or "").strip()
    if not next_due_raw:
        return [], None
    cursor: datetime | None = parse_iso_datetime(next_due_raw)
    due_candidates: list[datetime] = []
    safety = 0
    while cursor is not None and cursor <= now_dt and safety < 512:
        due_candidates.append(cursor)
        safety += 1
        if str(schedule["kind"]) == "at":
            cursor = None
            break
        cursor = compute_next_due(
            schedule=schedule,
            after=cursor,
            job_id=str(job["job_id"]),
            last_due=cursor,
        )
        if cursor is None:
            break

    policy = normalize_misfire_policy(job.get("misfire_policy"))
    lateness = max(0, int(job.get("max_lateness_s", 600)))
    cutoff = now_dt - timedelta(seconds=lateness)
    recent_candidates = [item for item in due_candidates if item >= cutoff]
    stale_only = bool(due_candidates) and not recent_candidates

    selected: list[datetime]
    if policy.mode == "catch_up":
        selected = recent_candidates[-max(1, int(policy.catch_up_max)) :]
        if not selected and stale_only and str(schedule["kind"]) == "at":
            selected = [now_dt]
    elif policy.mode == "skip":
        selected = recent_candidates[-1:]
    else:
        selected = recent_candidates[-1:]
        if not selected and stale_only:
            selected = [now_dt]

    next_due = cursor
    if str(schedule["kind"]) == "at":
        next_due = None
    elif next_due is None:
        next_due = compute_next_due(
            schedule=schedule,
            after=now_dt,
            job_id=str(job["job_id"]),
            last_due=now_dt,
        )
    return selected, next_due


def _deterministic_jitter_ms(job_id: str, dt: datetime, jitter_ms: int) -> int:
    if jitter_ms <= 0:
        return 0
    seed = f"{job_id}:{int(dt.timestamp() * 1000)}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()
    return int(digest[:8], 16) % (jitter_ms + 1)


def _deterministic_cron_stagger_ms(*, job_id: str, expr: str, stagger_ms: int) -> int:
    if stagger_ms <= 0:
        return 0
    seed = f"{job_id}:{expr}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()
    return int(digest[:8], 16) % (stagger_ms + 1)


@dataclass(frozen=True)
class _CronSpec:
    has_seconds: bool
    seconds: set[int]
    minutes: set[int]
    hours: set[int]
    days_of_month: set[int]
    months: set[int]
    days_of_week: set[int]
    dom_is_wildcard: bool
    dow_is_wildcard: bool


def _parse_cron_expression(expr: str) -> _CronSpec:
    fields = [part for part in expr.split() if part]
    if len(fields) not in {5, 6}:
        raise ValueError("cron expr must have 5 or 6 fields")

    has_seconds = len(fields) == 6
    if has_seconds:
        second_field, minute_field, hour_field, dom_field, month_field, dow_field = (
            fields
        )
    else:
        second_field = "0"
        minute_field, hour_field, dom_field, month_field, dow_field = fields

    seconds = _parse_cron_field(
        second_field, minimum=0, maximum=59, allow_sunday_7=False
    )
    minutes = _parse_cron_field(
        minute_field, minimum=0, maximum=59, allow_sunday_7=False
    )
    hours = _parse_cron_field(hour_field, minimum=0, maximum=23, allow_sunday_7=False)
    days_of_month = _parse_cron_field(
        dom_field, minimum=1, maximum=31, allow_sunday_7=False
    )
    months = _parse_cron_field(month_field, minimum=1, maximum=12, allow_sunday_7=False)
    days_of_week = _parse_cron_field(
        dow_field, minimum=0, maximum=6, allow_sunday_7=True
    )

    return _CronSpec(
        has_seconds=has_seconds,
        seconds=seconds,
        minutes=minutes,
        hours=hours,
        days_of_month=days_of_month,
        months=months,
        days_of_week=days_of_week,
        dom_is_wildcard=dom_field == "*",
        dow_is_wildcard=dow_field == "*",
    )


def _parse_cron_field(
    field_expr: str,
    *,
    minimum: int,
    maximum: int,
    allow_sunday_7: bool,
) -> set[int]:
    expr = str(field_expr or "").strip()
    if not expr:
        raise ValueError("cron field is empty")

    values: set[int] = set()
    for chunk in expr.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not _CRON_FIELD_RE.fullmatch(chunk):
            raise ValueError(f"unsupported cron field token: {chunk}")
        base, _, step_raw = chunk.partition("/")
        step = int(step_raw) if step_raw else 1
        if step <= 0:
            raise ValueError("cron step must be > 0")
        if base == "*":
            start = minimum
            end = maximum
        elif "-" in base:
            left, right = base.split("-", 1)
            start = int(left)
            end = int(right)
        else:
            start = int(base)
            end = int(base)
        if allow_sunday_7:
            start = 0 if start == 7 else start
            end = 0 if end == 7 else end
        if start < minimum or end > maximum or end < start:
            raise ValueError(f"cron field token out of range: {chunk}")
        values.update(range(start, end + 1, step))

    if not values:
        raise ValueError("cron field does not resolve to any values")
    return values


def _next_cron_occurrence(
    *, expr: str, tz_name: str, after: datetime
) -> datetime | None:
    spec = _parse_cron_expression(expr)
    tz = _load_timezone(tz_name)
    cursor = after.astimezone(tz)
    if spec.has_seconds:
        cursor = (cursor + timedelta(seconds=1)).replace(microsecond=0)
        step = timedelta(seconds=1)
    else:
        cursor = (cursor + timedelta(minutes=1)).replace(second=0, microsecond=0)
        step = timedelta(minutes=1)

    limit = cursor + timedelta(days=366 * 5)
    while cursor <= limit:
        if _cron_matches(spec, cursor):
            return cursor.astimezone(timezone.utc)
        cursor += step
    return None


def _cron_matches(spec: _CronSpec, local_dt: datetime) -> bool:
    if local_dt.second not in spec.seconds:
        return False
    if local_dt.minute not in spec.minutes:
        return False
    if local_dt.hour not in spec.hours:
        return False
    if local_dt.month not in spec.months:
        return False

    dom_match = local_dt.day in spec.days_of_month
    cron_weekday = (local_dt.weekday() + 1) % 7
    dow_match = cron_weekday in spec.days_of_week

    if spec.dom_is_wildcard and spec.dow_is_wildcard:
        return True
    if spec.dom_is_wildcard:
        return dow_match
    if spec.dow_is_wildcard:
        return dom_match
    return dom_match or dow_match


def _load_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown timezone: {name}") from exc


def _default_timezone_name() -> str:
    local = datetime.now().astimezone().tzinfo
    if local is not None:
        key = getattr(local, "key", None)
        if isinstance(key, str) and key.strip():
            return key
        token = str(datetime.now(local).tzname() or "").strip()
        if token:
            try:
                _load_timezone(token)
                return token
            except ValueError:
                pass
    return "UTC"


def _is_top_of_hour_expression(expr: str) -> bool:
    fields = [part for part in str(expr).split() if part]
    if len(fields) == 5:
        minute = fields[0]
        return _field_is_exact_zero(minute)
    if len(fields) == 6:
        second = fields[0]
        minute = fields[1]
        return _field_is_exact_zero(second) and _field_is_exact_zero(minute)
    return False


def _field_is_exact_zero(field_expr: str) -> bool:
    token = str(field_expr or "").strip()
    if not token:
        return False
    if any(separator in token for separator in (",", "/", "-", "*")):
        return False
    try:
        return int(token) == 0
    except ValueError:
        return False
