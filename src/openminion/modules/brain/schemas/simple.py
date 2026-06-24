"""Brain schema models for simplified decisions."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .decisions import (
    ActProfile,
    ClarifyContext,
    ExecutionTargetPayload,
    PendingTurnContext,
    RespondKind,
    normalize_decomposed_subtasks,
)
from .routing import _normalize_route_name


def _normalize_route_payload(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    if "route" not in normalized and "mode" in normalized:
        normalized["route"] = normalized.get("mode")
    normalized.pop("mode", None)
    return normalized


class _RoutePayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def normalize_route_alias(cls, value: Any) -> Any:
        return _normalize_route_payload(value)

    @field_validator("route", mode="before", check_fields=False)
    @classmethod
    def validate_route_name(cls, value: Any) -> Any:
        return _normalize_route_name(value)


class SimplifiedDecision(_RoutePayloadModel):
    model_config = ConfigDict(extra="ignore")

    route: str = Field(..., min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason_code: str = Field(default="progressive_retry", min_length=1)
    respond_kind: RespondKind | None = None
    answer: str | None = None
    question: str | None = None
    clarify_context: ClarifyContext | None = None
    pending_turn_context: PendingTurnContext | None = Field(
        default=None,
        description=(
            "Optional model-authored carry-forward context for the next user turn. "
            "Use only when a short follow-up should stay anchored to unfinished work."
        ),
    )
    act_profile: ActProfile | None = None
    execution_target: ExecutionTargetPayload | None = None


class UltraSimpleDecision(_RoutePayloadModel):
    model_config = ConfigDict(extra="ignore")

    route: str = Field(..., min_length=1)
    detail: str = ""


class _RespondPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    respond_kind: RespondKind
    answer: str = ""
    question: str = ""
    clarify_context: ClarifyContext | None = None
    pending_turn_context: PendingTurnContext | None = Field(
        default=None,
        description=(
            "Optional carry-forward context for the next user turn when the "
            "response leaves meaningful work in progress."
        ),
    )

    @model_validator(mode="after")
    def validate_shape(self) -> "_RespondPayload":
        if self.respond_kind == "answer" and not str(self.answer or "").strip():
            raise ValueError("answer is required when respond_kind=answer")
        if self.respond_kind == "clarify" and not str(self.question or "").strip():
            raise ValueError("question is required when respond_kind=clarify")
        return self


class _ActPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    act_profile: ActProfile | None = None
    execution_target: ExecutionTargetPayload | None = None
    max_steps_hint: int | None = Field(default=None, ge=1)
    rationale: str = ""
    subtasks: list[Any] = Field(default_factory=list)

    @field_validator("subtasks", mode="before")
    @classmethod
    def normalize_subtasks(cls, value: Any) -> Any:
        return normalize_decomposed_subtasks(value)

    @model_validator(mode="after")
    def validate_shape(self) -> "_ActPayload":
        if (
            self.execution_target is not None
            and self.execution_target.kind == "delegated"
            and not (
                str(self.execution_target.target_agent_id or "").strip()
                or str(self.execution_target.target_capability or "").strip()
            )
        ):
            raise ValueError(
                "target_agent_id or target_capability is required when delegated"
            )
        return self


def promote_to_full_decision(
    simplified: dict[str, Any], level: int
) -> dict[str, Any] | None:
    route = _normalize_route_name(simplified.get("route", simplified.get("mode")))
    confidence = simplified.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = 0.5
    reason_code = (
        str(simplified.get("reason_code") or "progressive_retry").strip()
        or "progressive_retry"
    )
    decision: dict[str, Any] = {
        "route": route,
        "confidence": float(max(0.0, min(1.0, confidence))),
        "reason_code": reason_code,
        "sub_intents": [],
        "rationale": "",
    }
    if level == 3:
        return None

    if route == "respond":
        respond_kind = str(simplified.get("respond_kind") or "").strip()
        if respond_kind not in {"answer", "clarify"}:
            return None
        decision["respond_kind"] = respond_kind
        if isinstance(simplified.get("clarify_context"), dict):
            decision["clarify_context"] = dict(simplified["clarify_context"])
        if isinstance(simplified.get("pending_turn_context"), dict):
            decision["pending_turn_context"] = dict(simplified["pending_turn_context"])
        if respond_kind == "clarify":
            decision["question"] = str(simplified.get("question") or "").strip()
        else:
            decision["answer"] = str(simplified.get("answer") or "").strip()
    elif route == "plan":
        # Compat bridge: plan -> act/orchestrate
        decision["route"] = "act"
        decision["act_profile"] = "orchestrate"
        decision["execution_target"] = {"kind": "local"}
        plan_hint = str(simplified.get("plan_hint") or "").strip()
        if plan_hint:
            decision["rationale"] = plan_hint
        subtasks = list(simplified.get("subtasks") or [])
        if subtasks:
            decision["subtasks"] = subtasks
        return decision
    elif route == "act":
        act_profile = str(simplified.get("act_profile") or "").strip()
        execution_target = simplified.get("execution_target")
        if act_profile and act_profile not in {
            "general",
            "coding",
            "research",
            "orchestrate",
        }:
            return None
        if act_profile:
            decision["act_profile"] = act_profile
        if isinstance(execution_target, dict):
            decision["execution_target"] = dict(execution_target)
        decision["rationale"] = str(simplified.get("rationale") or "").strip()
        subtasks = list(simplified.get("subtasks") or [])
        if subtasks:
            decision["subtasks"] = subtasks
    return decision


__all__ = [
    "SimplifiedDecision",
    "UltraSimpleDecision",
    "promote_to_full_decision",
]
