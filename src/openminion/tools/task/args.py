from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openminion.modules.task.scheduling.schedule import to_iso_utc, utc_now

from .constants import (
    DEFAULT_CONSOLIDATION_BATCH_LIMIT,
    DEFAULT_CONSOLIDATION_INTERVAL_HOURS,
    DEFAULT_WATCH_MAX_CHECKS,
    DEFAULT_WATCH_TIMEOUT_SECONDS,
    DEFAULT_WATCH_TTL_MINUTES,
    EVERY_UNIT_TO_MS,
)
from .routine.schemas import RoutinePayloadV1
from .scheduled_task.runtime import _text


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
    kind = _text(normalized.get("kind"))

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
            valid_delay = isinstance(delay_seconds, int) and not isinstance(
                delay_seconds, bool
            )
            if not valid_delay or delay_seconds <= 0:
                raise ValueError("after_seconds must be a positive integer")
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
        for drop_key, _ in _EVERY_SCHEDULE_ALIASES:
            normalized.pop(drop_key, None)
        normalized.pop("unit", None)
        return normalized

    return normalized


class TaskScheduleArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    instruction: str = Field(
        ..., min_length=1, description="Instruction to run at schedule time"
    )
    schedule: dict[str, Any] = Field(
        ...,
        description=(
            "Schedule object with a 'kind' field. "
            "For recurring: {kind: 'every', every_ms: <milliseconds>} "
            "or {kind: 'every', seconds: <n>} or {kind: 'every', minutes: <n>} or {kind: 'every', hours: <n>}. "
            "For cron: {kind: 'cron', expr: '<cron expression>'}. "
            "For one-shot: {kind: 'at', at: '<ISO 8601 datetime>'} or {kind: 'at', after_seconds: <n>}."
        ),
    )
    name: str | None = Field(default=None, description="Optional task name")
    goal_origin_action_type: Literal["watch", "task", "suggest", "none"] | None = Field(
        default=None,
        description=(
            "Optional: when this cron task is being created to back a "
            "recalled goal, set the action type so the runtime can apply "
            "agent_profile.goal_execution_policy. Omit for direct "
            "(non-goal) task scheduling."
        ),
    )

    @field_validator("instruction", mode="before")
    @classmethod
    def _normalize_instruction(cls, value: Any) -> str:
        token = _text(value)
        if not token:
            raise ValueError("instruction is required")
        return token

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: Any) -> str | None:
        token = _text(value)
        return token or None


class _TaskIdArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(..., min_length=1, description="Task identifier (cron job_id)")

    @field_validator("task_id", mode="before")
    @classmethod
    def _normalize_task_id(cls, value: Any) -> str:
        token = _text(value)
        if not token:
            raise ValueError("task_id is required")
        return token


class TaskCancelArgs(_TaskIdArgs):
    pass


class TaskListArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    limit: int = Field(default=20, description="Maximum number of tasks to return")

    @field_validator("limit", mode="before")
    @classmethod
    def _normalize_limit(cls, value: Any) -> int:
        if value is None:
            return 20
        return int(value)


class TaskPauseArgs(_TaskIdArgs):
    pass


class TaskResumeArgs(_TaskIdArgs):
    pass


class TaskShowArgs(_TaskIdArgs):
    runs_limit: int = Field(
        default=5, description="Maximum number of recent runs to return"
    )

    @field_validator("runs_limit", mode="before")
    @classmethod
    def _normalize_runs_limit(cls, value: Any) -> int:
        if value is None:
            return 5
        return int(value)


class TaskWatchArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str = Field(..., min_length=1, description="Short watch description")
    check_instruction: str = Field(
        ..., min_length=1, description="Instruction for each watch check turn"
    )
    interval_minutes: int = Field(..., ge=1, description="Polling interval in minutes")
    max_checks: int = Field(
        default=DEFAULT_WATCH_MAX_CHECKS,
        ge=1,
        description="Maximum number of checks before the watch expires",
    )
    alert_condition: str = Field(
        ...,
        min_length=1,
        description="Model-authored condition for triggering the alert",
    )
    delivery: str = Field(
        default="announce", description="Delivery mode: announce, webhook, or none"
    )
    check_profile_id: str | None = Field(
        default=None,
        description="Existing read-only tool exposure profile used for each check",
    )
    target_id: str | None = Field(
        default=None,
        description="Configured operations target bound to the selected check profile",
    )
    stop_on_condition: bool = Field(
        default=True,
        description=(
            "Whether the first true condition ends the watch. Set false for "
            "continuous monitoring; max_checks controls stopping after a "
            "fixed number of checks."
        ),
    )
    delivery_cooldown_minutes: int = Field(
        default=0,
        ge=0,
        le=10_080,
        description="Minimum minutes between repeated true-condition deliveries",
    )
    deliver_resolution: bool = Field(
        default=False,
        description="Request one delivery when an open condition returns to false",
    )
    on_condition_action: str | None = Field(
        default=None,
        description=(
            "Optional model-authored follow-up instruction to execute when the "
            "watch condition is met"
        ),
    )
    ttl_minutes: int = Field(
        default=DEFAULT_WATCH_TTL_MINUTES,
        ge=1,
        description="Maximum lifetime for the watch before expiry",
    )
    timeout_seconds: int = Field(
        default=DEFAULT_WATCH_TIMEOUT_SECONDS,
        ge=10,
        description="Maximum duration for each bounded check turn",
    )
    write_authorized: bool = Field(
        default=False,
        description=(
            "Operator-approved authorization for watch-triggered action turns to "
            "run write-capable tools without an interactive confirmation prompt."
        ),
    )
    goal_origin_action_type: Literal["watch", "task", "suggest", "none"] | None = Field(
        default=None,
        description=(
            "Optional: when this watch is being created to back a recalled "
            "goal, set the action type so the runtime can apply "
            "agent_profile.goal_execution_policy. Omit for direct "
            "(non-goal) watch creation."
        ),
    )
    routine: RoutinePayloadV1 | None = Field(
        default=None,
        description=(
            "Optional typed routine object. For a social "
            "signal watch, set routine_kind='social_signal', provide "
            "config.sources and config.topics, and set stop_on_condition=false "
            "for continuous monitoring. Unknown routine_kind values fail "
            "validation deterministically (no silent degradation)."
        ),
    )

    @model_validator(mode="after")
    def _validate_routine_specific_rules(self) -> "TaskWatchArgs":
        if self.routine is not None and self.routine.routine_kind == "github_pr_review":
            if self.interval_minutes < 5:
                raise ValueError(
                    "routine_kind='github_pr_review' requires interval_minutes >= 5"
                )
        social_routine = (
            self.routine is not None and self.routine.routine_kind == "social_signal"
        )
        if social_routine:
            if self.interval_minutes < 15:
                raise ValueError(
                    "routine_kind='social_signal' requires interval_minutes >= 15"
                )
            if self.stop_on_condition:
                raise ValueError(
                    "routine_kind='social_signal' requires stop_on_condition=false"
                )
            if self.check_profile_id or self.target_id:
                raise ValueError(
                    "social_signal does not use infrastructure profiles or targets"
                )
            if self.write_authorized or self.on_condition_action:
                raise ValueError(
                    "social_signal does not allow writes or on-condition actions"
                )
        profile_bound = self.check_profile_id is not None or self.target_id is not None
        if profile_bound and not (self.check_profile_id and self.target_id):
            raise ValueError("check_profile_id and target_id must be provided together")
        if not self.stop_on_condition and not profile_bound and not social_routine:
            raise ValueError(
                "continuous monitoring requires check_profile_id and target_id"
            )
        if profile_bound and (self.write_authorized or self.on_condition_action):
            raise ValueError(
                "profile-bound monitoring does not allow writes or on-condition actions"
            )
        return self

    @field_validator(
        "description",
        "check_instruction",
        "alert_condition",
        "delivery",
        mode="before",
    )
    @classmethod
    def _normalize_text_fields(cls, value: Any) -> str:
        token = _text(value)
        if not token:
            raise ValueError("value is required")
        return token

    @field_validator("delivery", mode="after")
    @classmethod
    def _validate_delivery(cls, value: str) -> str:
        token = value.strip().lower()
        if token not in {"announce", "webhook", "none"}:
            raise ValueError("delivery must be announce, webhook, or none")
        return token

    @field_validator("on_condition_action", mode="before")
    @classmethod
    def _normalize_optional_action(cls, value: Any) -> str | None:
        token = _text(value)
        return token or None

    @field_validator("check_profile_id", "target_id", mode="before")
    @classmethod
    def _normalize_optional_identifier(cls, value: Any) -> str | None:
        token = _text(value)
        return token or None


class TaskConsolidateMemoryArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    interval_hours: int = Field(
        default=DEFAULT_CONSOLIDATION_INTERVAL_HOURS,
        ge=1,
        description="How often to run consolidation, in hours",
    )
    batch_limit: int = Field(
        default=DEFAULT_CONSOLIDATION_BATCH_LIMIT,
        ge=1,
        le=15,
        description="Maximum number of memory candidates to review per consolidation run",
    )
    name: str | None = Field(
        default=None,
        description="Optional task name for the recurring consolidation job",
    )

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: Any) -> str | None:
        token = _text(value)
        return token or None


__all__ = [
    "TaskCancelArgs",
    "TaskConsolidateMemoryArgs",
    "TaskListArgs",
    "TaskPauseArgs",
    "TaskResumeArgs",
    "TaskScheduleArgs",
    "TaskShowArgs",
    "TaskWatchArgs",
]
