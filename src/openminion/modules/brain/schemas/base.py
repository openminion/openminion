from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from openminion.base.time import utc_now_iso


def iso_now() -> str:
    return utc_now_iso()


def new_uuid() -> str:
    return str(uuid.uuid4())


RiskLevel = Literal["low", "med", "high"]
ActionStatus = Literal["success", "retry", "failed", "blocked", "needs_user", "timeout"]
CommandKind = Literal["tool", "agent", "ask_user", "finish", "think"]


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(..., min_length=1)
    label: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class BaseCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(default_factory=new_uuid, min_length=1)
    kind: CommandKind
    title: str = Field(..., min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    success_criteria: dict[str, Any] = Field(default_factory=dict)
    fallback: dict[str, Any] | None = None
    disposition: str | None = None
    risk_level: RiskLevel = "low"
    requires_confirmation: bool = False
    idempotency_key: str = ""
    timeout_ms: int | None = Field(default=None, ge=1)
    skill_id: str | None = Field(
        default=None,
        description=(
            "Optional explicit skill binding for this plan step. Runtime may use "
            "only this typed id, never command prose, to activate a skill context."
        ),
    )
    sub_intent_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Internal stable sub-intent IDs served by this command. "
            "Used for downstream plan/execution bookkeeping during the "
            "structured sub-intent migration."
        ),
    )

    @field_validator("sub_intent_ids", mode="before")
    @classmethod
    def _normalize_sub_intent_ids(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, tuple):
            return list(value)
        return value

    @field_validator("skill_id", mode="before")
    @classmethod
    def _normalize_skill_id(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value or "").strip()
        return text or None

    @model_validator(mode="after")
    def _dedupe_sub_intent_ids(self) -> "BaseCommand":
        normalized: list[str] = []
        for raw_value in self.sub_intent_ids:
            text = str(raw_value or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        self.sub_intent_ids = normalized
        return self
