from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from .runtime.turn import feasibility as _feasibility_runtime
from openminion.modules.prompting.continuation import build_feasibility_choice_prompt
from ..diagnostics.events import CanonicalEventLogger
from ..schemas import (
    ActDecision,
    Command,
    Decision,
    FeasibilityReport,
    Plan,
    SubIntent,
    WorkingState,
    build_intent_execution_states,
    feasibility_report_payload,
    normalize_sub_intent_ids,
    select_sub_intents_by_ids,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


_TRANSIENT_FACT_FIELDS = {
    "auth_status": {"missing", "expired"},
    "runtime_status": {"unavailable", "degraded"},
    "rate_limit_state": {"limited", "exhausted"},
    "config_status": {"missing", "invalid"},
}
_CHOICE_TOKENS = frozenset({"continue", "retry", "cancel", "skip"})


def build_runtime_supplement(
    *,
    tool_schemas: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Return transient runtime/tool facts without re-deriving semantic coverage."""

    findings: list[dict[str, Any]] = []
    for entry in tool_schemas or []:
        if not isinstance(entry, Mapping):
            continue
        tool_name = str(entry.get("name", "") or "").strip()
        if not tool_name:
            continue
        for field, blocking_values in _TRANSIENT_FACT_FIELDS.items():
            value = str(entry.get(field, "") or "").strip().lower()
            if value not in blocking_values:
                continue
            findings.append(
                {
                    "tool_name": tool_name,
                    "kind": field,
                    "value": value,
                    "details": (
                        dict(entry.get("metadata_details", {}))
                        if isinstance(entry.get("metadata_details"), Mapping)
                        else {}
                    ),
                }
            )
    return findings


def extract_feasibility_report(
    state_payload: Mapping[str, Any] | None,
) -> FeasibilityReport | None:
    payload = feasibility_report_payload(state_payload or {})
    if not payload:
        return None
    try:
        return FeasibilityReport.model_validate(payload)
    except Exception:
        return None


def feasibility_state_flag(
    state_payload: Mapping[str, Any] | None,
    key: str,
) -> bool:
    if not isinstance(state_payload, Mapping):
        return False
    return bool(state_payload.get(key))


def has_pending_feasibility_choice(state: WorkingState) -> bool:
    payload = getattr(state, "decision_feasibility_state", {})
    return feasibility_state_flag(payload, "awaiting_user_choice")


def parse_feasibility_choice(text: str | None) -> str:
    normalized = str(text or "").strip().lower()
    return normalized if normalized in _CHOICE_TOKENS else "unclear"


def clear_feasibility_state(state: WorkingState) -> None:
    state.decision_feasibility_state = {}
    state.decision_feasibility_report = None


def feasibility_user_message(report: FeasibilityReport | None) -> str:
    if report is None:
        return "User guidance is required before this request can continue."
    if str(report.user_message or "").strip():
        return str(report.user_message).strip()
    if report.blocked_intent_ids and report.viable_intent_ids:
        return "Current tools cover part of this request, but some steps are blocked."
    if report.blocked_intent_ids:
        return "Current tool coverage or runtime state cannot complete this request."
    return "Current plan is viable."


def feasibility_choice_message(report: FeasibilityReport | None) -> str:
    return str(
        build_feasibility_choice_prompt(user_message=feasibility_user_message(report))
    )


def apply_viable_subset(state: WorkingState, report: FeasibilityReport) -> bool:
    viable_ids = set(report.viable_intent_ids)
    if not viable_ids:
        return False
    if state.plan is None:
        return False

    filtered_steps = [
        step.model_copy(deep=True)
        for step in state.plan.steps
        if not getattr(step, "sub_intent_ids", None)
        or viable_ids.intersection(set(getattr(step, "sub_intent_ids", []) or []))
    ]
    if not filtered_steps:
        return False

    selected_sub_intents = select_sub_intents_by_ids(
        getattr(state, "decision_sub_intent_refs", [])
        or getattr(state.plan, "sub_intents", []),
        report.viable_intent_ids,
    )
    if not selected_sub_intents:
        selected_sub_intents = select_sub_intents_by_ids(
            getattr(state.plan, "sub_intents", []),
            report.viable_intent_ids,
        )
    if not selected_sub_intents:
        return False

    next_cursor = min(
        max(0, int(getattr(state, "cursor", 0) or 0)), max(len(filtered_steps) - 1, 0)
    )
    state.plan = state.plan.model_copy(
        update={
            "steps": filtered_steps,
            "sub_intents": list(selected_sub_intents),
        },
        deep=True,
    )
    state.cursor = next_cursor
    state.decision_sub_intents = [
        str(getattr(item, "description", "") or "").strip()
        or str(getattr(item, "id", "") or "").strip()
        for item in selected_sub_intents
        if str(getattr(item, "description", "") or "").strip()
        or str(getattr(item, "id", "") or "").strip()
    ]
    state.decision_sub_intent_refs = list(selected_sub_intents)
    state.adaptive_satisfied_intent_ids = [
        item
        for item in list(getattr(state, "adaptive_satisfied_intent_ids", []) or [])
        if item in viable_ids
    ]
    state.intent_execution_states = build_intent_execution_states(
        selected_sub_intents,
        existing=getattr(state, "intent_execution_states", []) or [],
    )
    return True


def assess_step_feasibility(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    command: Command,
    user_input: str | None,
    logger: CanonicalEventLogger,
) -> FeasibilityReport | None:
    source_sub_intents = list(getattr(state, "decision_sub_intent_refs", []) or [])
    command_intent_ids = normalize_sub_intent_ids(
        getattr(command, "sub_intent_ids", []) or []
    )
    if command_intent_ids:
        selected = select_sub_intents_by_ids(source_sub_intents, command_intent_ids)
    else:
        selected = []
    if not selected and source_sub_intents:
        selected = list(source_sub_intents)
    probe_state = state.model_copy(deep=True)
    probe_sub_intents: list[SubIntent] = list(selected)
    probe_state.plan = Plan(
        objective=str(
            getattr(state, "goal", "")
            or getattr(command, "title", "")
            or "step feasibility check"
        ),
        steps=[command.model_copy(deep=True)],
        stop_conditions=["step_checked"],
        assumptions=[],
        risk_summary="step feasibility probe",
        success_criteria={"status": "success"},
        sub_intents=probe_sub_intents,
    )
    probe_state.cursor = 0
    probe_state.decision_sub_intent_refs = list(probe_sub_intents)
    probe_state.intent_execution_states = build_intent_execution_states(
        probe_sub_intents,
        existing=getattr(state, "intent_execution_states", []) or [],
    )
    return assess_plan_feasibility(
        runner,
        state=probe_state,
        user_input=user_input,
        logger=logger,
    )


def is_hard_infeasibility(report: FeasibilityReport | None) -> bool:
    if report is None:
        return False
    if report.plan_viable:
        return False
    if report.viable_intent_ids:
        return False
    return report.recommendation in {
        "retry_full",
        "abort",
        "suggest_alternatives",
        "proceed_full",
    }


def build_resume_decision(state: WorkingState) -> Decision | None:
    if state.plan is None:
        return None
    current_step = None
    if 0 <= int(getattr(state, "cursor", 0) or 0) < len(state.plan.steps):
        current_step = state.plan.steps[int(getattr(state, "cursor", 0) or 0)]
    if current_step is None:
        return None
    decision = ActDecision(
        confidence=1.0,
        reason_code="resume_existing_plan",
        sub_intents=[],
        rationale=str(getattr(state, "decision_rationale", "") or "").strip(),
    )
    decision._seeded_commands = [current_step.model_copy(deep=True)]
    return decision


def serialize_feasibility_state(
    report: FeasibilityReport,
    *,
    awaiting_user_choice: bool = False,
    reviewed: bool = True,
    approved_subset: bool = False,
) -> dict[str, Any]:
    payload = report.model_dump(mode="json")
    payload["awaiting_user_choice"] = bool(awaiting_user_choice)
    payload["reviewed"] = bool(reviewed)
    payload["approved_subset"] = bool(approved_subset)
    return payload


def _simple_single_step_feasibility(
    *,
    state: WorkingState,
    runtime_tool_schemas: list[dict[str, Any]],
    runtime_facts: list[dict[str, Any]],
    structured_sub_intents: list[Any],
) -> FeasibilityReport | None:
    """Fast-path typed feasibility for one-step local tool plans."""
    if runtime_facts:
        return None
    if (
        state.plan is None
        or len(state.plan.steps) != 1
        or len(structured_sub_intents) != 1
    ):
        return None

    step = state.plan.steps[0]
    if getattr(step, "kind", "") != "tool":
        return None

    tool_name = str(getattr(step, "tool_name", "") or "").strip()
    if not tool_name:
        return None
    available_tools = {
        str(item.get("name", "")).strip()
        for item in runtime_tool_schemas
        if isinstance(item, Mapping) and str(item.get("name", "")).strip()
    }
    if tool_name not in available_tools:
        return None

    intent = structured_sub_intents[0]
    intent_id = str(getattr(intent, "id", "") or "").strip()
    if not intent_id:
        return None
    step_intent_ids = list(getattr(step, "sub_intent_ids", []) or [])
    if step_intent_ids and step_intent_ids != [intent_id]:
        return None

    return FeasibilityReport(
        plan_viable=True,
        recommendation="proceed_full",
        user_message="",
        requires_user_choice=False,
        viable_intent_ids=[intent_id],
        blocked_intent_ids=[],
        assessments=[
            {
                "intent_id": intent_id,
                "status": "covered",
                "reason": "Single tool step is directly available in the current runtime.",
                "covering_tools": [tool_name],
            }
        ],
    )


def assess_plan_feasibility(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_input: str | None,
    logger: CanonicalEventLogger,
) -> FeasibilityReport | None:
    return _feasibility_runtime.assess_plan_feasibility(
        runner,
        state=state,
        user_input=user_input,
        logger=logger,
    )
