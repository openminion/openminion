"""Explicit agent-led self-compaction step for future-self summaries."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from openminion.modules.brain.loop.constants import (
    SELF_COMPACTION_EVENT_TYPE,
    SELF_COMPACTION_MAX_CHARS,
)
from openminion.modules.brain.constants import STATE_KEY_MODULE_STATE
from openminion.modules.context.compress.eligibility import (
    CompactionBudgetState,
    EligibilityResult,
)
from openminion.modules.llm.schemas import Message
from openminion.modules.memory import MAINTENANCE_MODULE_STATE_KEY


@dataclass(frozen=True)
class SelfCompactionResult:
    applied: bool
    reason_code: str
    summary_text: str = ""
    marker: str = ""
    state_hash: str = ""
    audit_payload: dict[str, Any] = field(default_factory=dict)


def _fit_summary(text: str, *, max_chars: int = SELF_COMPACTION_MAX_CHARS) -> str:
    rendered = " ".join(str(text or "").strip().split())
    if len(rendered) <= max_chars:
        return rendered
    trimmed = rendered[: max(0, max_chars - 3)].rstrip()
    return f"{trimmed}..."


def _maintenance_bucket(working_state: Any) -> dict[str, Any]:
    module_state = getattr(working_state, STATE_KEY_MODULE_STATE, None)
    if not isinstance(module_state, dict):
        module_state = {}
        setattr(working_state, STATE_KEY_MODULE_STATE, module_state)
    maintenance = module_state.get(MAINTENANCE_MODULE_STATE_KEY)
    if not isinstance(maintenance, dict):
        maintenance = {}
        module_state[MAINTENANCE_MODULE_STATE_KEY] = maintenance
    return maintenance


def _audit_payload(
    *,
    working_state: Any,
    marker: str,
    state_hash: str,
    input_ref: str,
    output_ref: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "operation": "self_compaction",
        "session_id": str(getattr(working_state, "session_id", "") or "").strip(),
        "turn_id": str(getattr(working_state, "trace_id", "") or "").strip(),
        "marker": str(marker or "").strip(),
        "state_hash": str(state_hash or "").strip(),
        "input_ref": str(input_ref or "").strip(),
        "output_ref": str(output_ref or "").strip(),
        "reason": str(reason or "").strip(),
    }


def emit_self_compaction_audit_event(
    session_api: Any | None,
    *,
    working_state: Any,
    payload: dict[str, Any],
) -> None:
    append_event = getattr(session_api, "append_event", None)
    if not callable(append_event):
        return
    try:
        append_event(
            str(getattr(working_state, "session_id", "") or ""),
            SELF_COMPACTION_EVENT_TYPE,
            dict(payload),
            actor_type="agent",
            actor_id=str(getattr(working_state, "agent_id", "") or ""),
            trace={"trace_id": str(getattr(working_state, "trace_id", "") or "")}
            if str(getattr(working_state, "trace_id", "") or "").strip()
            else None,
            importance=2,
            redaction="none",
            status="ok",
        )
    except Exception:  # noqa: BLE001
        return


def apply_self_compaction_result(
    working_state: Any,
    *,
    eligibility: EligibilityResult,
    summary_text: str,
    session_api: Any | None = None,
    now: datetime | None = None,
    reason: str = "token_pressure",
    input_ref: str = "",
    output_ref: str = "",
) -> SelfCompactionResult:
    if not eligibility.is_eligible:
        return SelfCompactionResult(
            applied=False,
            reason_code=eligibility.reason_code,
            state_hash=eligibility.state_hash,
        )
    compacted = _fit_summary(summary_text)
    if not compacted:
        return SelfCompactionResult(
            applied=False,
            reason_code="EMPTY_SUMMARY",
            state_hash=eligibility.state_hash,
        )
    maintenance = _maintenance_bucket(working_state)
    marker_dt = now or datetime.now(timezone.utc)
    prior_consolidation = str(
        maintenance.get("last_consolidation_marker", "") or ""
    ).strip()
    if prior_consolidation:
        try:
            prior_dt = datetime.fromisoformat(prior_consolidation)
        except ValueError:
            prior_dt = None
        if prior_dt is not None and prior_dt >= marker_dt:
            marker_dt = prior_dt + timedelta(seconds=1)
    marker = marker_dt.isoformat()
    working_state.session_work_summary = compacted
    maintenance["last_compaction_marker"] = marker
    maintenance["last_compaction_state_hash"] = eligibility.state_hash
    payload = _audit_payload(
        working_state=working_state,
        marker=marker,
        state_hash=eligibility.state_hash,
        input_ref=input_ref,
        output_ref=output_ref,
        reason=reason,
    )
    emit_self_compaction_audit_event(
        session_api,
        working_state=working_state,
        payload=payload,
    )
    return SelfCompactionResult(
        applied=True,
        reason_code="OK",
        summary_text=compacted,
        marker=marker,
        state_hash=eligibility.state_hash,
        audit_payload=payload,
    )


def run_self_compaction_step(
    *,
    working_state: Any,
    runtime: Any,
    model: str,
    context_service: Any,
    prompt_token_estimate: int,
    budget_state: CompactionBudgetState,
    session_api: Any | None = None,
    now: datetime | None = None,
    recent_work: str = "",
    reason: str = "token_pressure",
) -> SelfCompactionResult:
    target_now = now or datetime.now(timezone.utc)
    eligibility = context_service.evaluate_self_compaction_eligibility(
        working_state=working_state,
        prompt_token_estimate=prompt_token_estimate,
        budget_state=budget_state,
        now=target_now,
    )
    if not eligibility.is_eligible:
        return SelfCompactionResult(
            applied=False,
            reason_code=eligibility.reason_code,
            state_hash=eligibility.state_hash,
        )
    prompt = "\n".join(
        part
        for part in [
            "Summarize the current work for your future self.",
            "Return plain text only.",
            "Keep it concise, under 800 characters, and focus on what is done, what remains, active blockers, and the next concrete step.",
            f"Goal: {str(getattr(working_state, 'goal', '') or '').strip()}",
            f"Current session work summary: {str(getattr(working_state, 'session_work_summary', '') or '').strip()}",
            f"Recent work: {str(recent_work or '').strip()}",
        ]
        if str(part or "").strip()
    )
    response = runtime.complete(
        messages=[
            Message(role="system", content="Produce a future-self checkpoint."),
            Message(role="user", content=prompt),
        ],
        tools=[],
        model=model,
        tool_choice="none",
        max_output_tokens=min(220, max(32, int(budget_state.max_prompt_tokens or 0))),
        metadata={"purpose": "self_compaction"},
    )
    summary_text = str(getattr(response, "output_text", "") or "").strip()
    return apply_self_compaction_result(
        working_state,
        eligibility=eligibility,
        summary_text=summary_text,
        session_api=session_api,
        now=target_now,
        reason=reason,
        input_ref=f"prompt:{max(0, int(prompt_token_estimate or 0))}",
        output_ref=f"summary:{len(summary_text)}",
    )
