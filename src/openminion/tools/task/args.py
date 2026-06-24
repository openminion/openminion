from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .constants import (
    DEFAULT_CONSOLIDATION_BATCH_LIMIT,
    DEFAULT_CONSOLIDATION_INTERVAL_HOURS,
    DEFAULT_WATCH_MAX_CHECKS,
    DEFAULT_WATCH_TIMEOUT_SECONDS,
    DEFAULT_WATCH_TTL_MINUTES,
)
from .routine.schemas import RoutinePayloadV1
from .scheduled_task.runtime import _text


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
            "For one-shot: {kind: 'at', at: '<ISO 8601 datetime>'}."
        ),
    )
    name: str | None = Field(default=None, description="Optional task name")
    goal_origin_action_type: Optional[Literal["watch", "task", "suggest", "none"]] = (
        Field(
            default=None,
            description=(
                "Optional: when this cron task is being created to back a "
                "recalled goal, set the action type so the runtime can apply "
                "agent_profile.goal_execution_policy. Omit for direct "
                "(non-goal) task scheduling."
            ),
        )
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
        if value is None:
            return None
        token = str(value).strip()
        return token or None


class TaskCancelArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(..., min_length=1, description="Task identifier (cron job_id)")

    @field_validator("task_id", mode="before")
    @classmethod
    def _normalize_task_id(cls, value: Any) -> str:
        token = _text(value)
        if not token:
            raise ValueError("task_id is required")
        return token


class TaskListArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    limit: int = Field(default=20, description="Maximum number of tasks to return")

    @field_validator("limit", mode="before")
    @classmethod
    def _normalize_limit(cls, value: Any) -> int:
        if value is None:
            return 20
        return int(value)


class TaskPauseArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(..., min_length=1, description="Task identifier (cron job_id)")

    @field_validator("task_id", mode="before")
    @classmethod
    def _normalize_task_id(cls, value: Any) -> str:
        token = _text(value)
        if not token:
            raise ValueError("task_id is required")
        return token


class TaskResumeArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(..., min_length=1, description="Task identifier (cron job_id)")

    @field_validator("task_id", mode="before")
    @classmethod
    def _normalize_task_id(cls, value: Any) -> str:
        token = _text(value)
        if not token:
            raise ValueError("task_id is required")
        return token


class TaskShowArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(..., min_length=1, description="Task identifier (cron job_id)")
    runs_limit: int = Field(
        default=5, description="Maximum number of recent runs to return"
    )

    @field_validator("task_id", mode="before")
    @classmethod
    def _normalize_task_id(cls, value: Any) -> str:
        token = _text(value)
        if not token:
            raise ValueError("task_id is required")
        return token

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
    goal_origin_action_type: Optional[Literal["watch", "task", "suggest", "none"]] = (
        Field(
            default=None,
            description=(
                "Optional: when this watch is being created to back a recalled "
                "goal, set the action type so the runtime can apply "
                "agent_profile.goal_execution_policy. Omit for direct "
                "(non-goal) watch creation."
            ),
        )
    )
    routine: Optional[RoutinePayloadV1] = Field(
        default=None,
        description=(
            "Optional typed routine binding. V1 supports "
            '`routine_kind = "github_pr_review"`. Unknown routine_kind values '
            "fail validation deterministically (no silent degradation)."
        ),
    )

    @model_validator(mode="after")
    def _validate_routine_specific_rules(self) -> "TaskWatchArgs":
        if self.routine is not None and self.routine.routine_kind == "github_pr_review":
            if int(self.interval_minutes) < 5:
                raise ValueError(
                    "routine_kind='github_pr_review' requires interval_minutes >= 5"
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
        token = str(value or "").strip().lower()
        if token not in {"announce", "webhook", "none"}:
            raise ValueError("delivery must be announce, webhook, or none")
        return token

    @field_validator("on_condition_action", mode="before")
    @classmethod
    def _normalize_optional_action(cls, value: Any) -> str | None:
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
        if value is None:
            return None
        token = str(value).strip()
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
