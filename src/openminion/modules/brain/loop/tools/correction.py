from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class CorrectionPlan(BaseModel, extra="forbid"):
    """Structured correction plan returned by Layer 2 macro-correction."""

    diagnosis: str = Field(..., min_length=1)
    correction_type: Literal[
        "retry_same",
        "retry_different",
        "replan",
        "ask_user",
        "accept_partial",
    ]
    corrected_args: dict | None = None
    replan_hint: str | None = None
    confidence: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_cross_field_invariants(self) -> "CorrectionPlan":
        if self.correction_type == "retry_different" and not self.corrected_args:
            raise ValueError(
                "corrected_args is required when correction_type is 'retry_different'"
            )
        if self.correction_type == "replan" and not self.replan_hint:
            raise ValueError("replan_hint is required when correction_type is 'replan'")
        return self


class CorrectionRecord(BaseModel, extra="forbid"):
    """One entry in the correction history."""

    iteration_index: int
    correction_type: Literal[
        "retry_same",
        "retry_different",
        "replan",
        "ask_user",
        "accept_partial",
    ]
    diagnosis_summary: str
    applied: bool


class CorrectionHistory(BaseModel):
    """Running history of corrections applied during a loop invocation."""

    records: list[CorrectionRecord] = Field(default_factory=list)

    def append(self, record: CorrectionRecord) -> None:
        self.records.append(record)

    def last_n(self, n: int) -> list[CorrectionRecord]:
        return self.records[-n:] if n > 0 else []

    def __len__(self) -> int:
        return len(self.records)


def trigger_macro_correction(
    *,
    loop_ctx: Any,
    profile: Any,  # AdaptiveToolLoopProfile
    loop_state: Any,  # AdaptiveToolLoopState
    failure_context: str,
    model: str,
    runtime: Any,
    messages: list,
) -> CorrectionPlan | None:
    """Attempt a Layer 2 macro-correction LLM call."""
    macro_count = loop_state.scratchpad.get("macro_correction_count", 0)

    if macro_count >= profile.max_macro_corrections:
        return None

    last_macro_iteration = loop_state.scratchpad.get("last_macro_iteration", -999)
    if (
        loop_state.iteration - last_macro_iteration
    ) < profile.macro_correction_cooldown:
        return None

    correction_model = profile.reflection_model or model
    correction_prompt = [
        {
            "role": "system",
            "content": "You are diagnosing a tool execution failure. Return a JSON CorrectionPlan.",
        },
        {
            "role": "user",
            "content": (
                f"The following failure occurred:\n{failure_context}\n\n"
                "Return a JSON object with: diagnosis (string), correction_type "
                "(one of: retry_same, retry_different, replan, ask_user, accept_partial), "
                "corrected_args (dict, required if retry_different), "
                "replan_hint (string, required if replan), confidence (float 0-1)."
            ),
        },
    ]

    plan: CorrectionPlan | None = None
    try:
        response = runtime.complete(
            messages=correction_prompt,
            tools=[],
            model=correction_model,
        )
        if isinstance(response, dict):
            content = response.get("content", "")
        else:
            content = str(
                getattr(response, "content", None)
                or getattr(response, "output_text", "")
                or ""
            )
        plan_data = json.loads(content)
        plan = CorrectionPlan.model_validate(plan_data)
    except Exception:  # noqa: BLE001
        plan = None

    loop_state.scratchpad["macro_correction_count"] = macro_count + 1
    loop_state.scratchpad["last_macro_iteration"] = loop_state.iteration

    return plan


def dispatch_correction_plan(
    *,
    plan: CorrectionPlan,
    loop_ctx: Any,
    loop_state: Any,
    messages: list,
    last_tool_call: Any | None = None,
    profile: Any,
) -> str | None:
    """Act on a CorrectionPlan. Returns termination reason or None to continue."""
    from .contracts import ADAPTIVE_TERM_FINAL_TEXT, ADAPTIVE_TERM_NEEDS_USER
    from openminion.modules.llm.schemas import Message

    record = CorrectionRecord(
        iteration_index=loop_state.iteration,
        correction_type=plan.correction_type,
        diagnosis_summary=plan.diagnosis[:200],
        applied=True,
    )
    history = loop_state.scratchpad.setdefault("correction_history", [])
    history.append(record.model_dump())

    if plan.correction_type == "retry_same":
        return None

    if plan.correction_type == "retry_different":
        if not plan.corrected_args:
            raise ValueError("retry_different correction plan requires corrected_args")
        messages.append(
            Message(
                role="system",
                content=(
                    f"[system] Correction applied: retrying with modified arguments: "
                    f"{json.dumps(plan.corrected_args)}"
                ),
            )
        )
        return None

    if plan.correction_type == "replan":
        messages.append(
            Message(
                role="system",
                content=f"[system] Replanning: {plan.replan_hint}",
            )
        )
        loop_state.iteration = 0
        return None

    if plan.correction_type == "ask_user":
        return ADAPTIVE_TERM_NEEDS_USER

    if plan.correction_type == "accept_partial":
        return ADAPTIVE_TERM_FINAL_TEXT

    return None


def build_correction_history_summary(scratchpad: dict) -> str | None:
    """Build a system message summarising the last five correction records."""
    history: list[dict] = scratchpad.get("correction_history", [])
    if not history:
        return None
    last_five = history[-5:]
    lines = ["[system] Prior corrections in this loop:"]
    for entry in last_five:
        lines.append(
            f"  - iteration {entry.get('iteration_index')}: "
            f"{entry.get('correction_type')} — {entry.get('diagnosis_summary', '')}"
        )
    return "\n".join(lines)
