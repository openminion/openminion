"""Turn-feasibility runtime implementation."""

from typing import TYPE_CHECKING, Any

from ....diagnostics.events import CanonicalEventLogger
from ....retry import call_structured_with_retry
from ....schemas import FeasibilityReport, WorkingState, new_uuid

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....runner import BrainRunner


def assess_plan_feasibility(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_input: str | None,
    logger: CanonicalEventLogger,
) -> FeasibilityReport | None:
    structured_sub_intents = _structured_sub_intents(state)
    if (
        runner.llm_api is None
        or runner.context_api is None
        or state.plan is None
        or not structured_sub_intents
    ):
        return None
    feasibility_barrel = _feasibility_barrel()
    runtime_tool_schemas = runner._collect_runtime_tool_schemas()
    runtime_facts = feasibility_barrel.build_runtime_supplement(
        tool_schemas=runtime_tool_schemas
    )
    shortcut = feasibility_barrel._simple_single_step_feasibility(
        state=state,
        runtime_tool_schemas=runtime_tool_schemas,
        runtime_facts=runtime_facts,
        structured_sub_intents=structured_sub_intents,
    )
    if shortcut is not None:
        _emit_feasibility_shortcut(logger=logger, state=state, shortcut=shortcut)
        return shortcut
    llm_call_id = new_uuid()
    model = runner.profile.llm_profiles.reflect_model
    hints = _feasibility_hints(
        runner=runner,
        state=state,
        user_input=user_input,
        llm_call_id=llm_call_id,
        runtime_tool_schemas=runtime_tool_schemas,
        runtime_facts=runtime_facts,
        structured_sub_intents=structured_sub_intents,
    )
    logger.emit(
        "brain.feasibility.started",
        {
            "llm_call_id": llm_call_id,
            "model": model,
            "intent_count": len(structured_sub_intents),
        },
        trace_id=state.trace_id,
    )
    try:
        context = runner._build_context(
            state=state,
            purpose="validate",
            budget={"max_tokens": min(1800, state.budgets_remaining.tokens)},
            hints=hints,
            logger=logger,
        )
        raw = call_structured_with_retry(
            runner.llm_api,
            model=model,
            purpose="validate",
            context=context,
            schema=FeasibilityReport,
        )
        state.llm_calls_used += 1
        if isinstance(raw, dict):
            runner._debit_tokens(state, raw, logger)
        return _validate_feasibility_report(
            state=state,
            logger=logger,
            llm_call_id=llm_call_id,
            report=FeasibilityReport.model_validate(raw),
            structured_sub_intents=structured_sub_intents,
        )
    except Exception as exc:
        logger.emit(
            "brain.feasibility.failed",
            {"llm_call_id": llm_call_id, "error": str(exc)},
            trace_id=state.trace_id,
            status="warning",
        )
        return _fallback_feasibility_report(
            structured_sub_intents=structured_sub_intents
        )


def _structured_sub_intents(state: WorkingState) -> list[Any]:
    structured_sub_intents = list(getattr(state, "decision_sub_intent_refs", []) or [])
    if structured_sub_intents:
        return structured_sub_intents
    return list(getattr(getattr(state, "plan", None), "sub_intents", []) or [])


def _emit_feasibility_shortcut(
    *, logger: CanonicalEventLogger, state: WorkingState, shortcut: FeasibilityReport
) -> None:
    logger.emit(
        "brain.feasibility.shortcut",
        {
            "recommendation": shortcut.recommendation,
            "tool_name": str(
                getattr(state.plan.steps[0], "tool_name", "") or ""
            ).strip(),
        },
        trace_id=state.trace_id,
    )


def _feasibility_hints(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    user_input: str | None,
    llm_call_id: str,
    runtime_tool_schemas: list[Any],
    runtime_facts: dict[str, Any],
    structured_sub_intents: list[Any],
) -> dict[str, Any]:
    return {
        "_llm_call_id": llm_call_id,
        "current_datetime": "",
        "user_input": str(user_input or state.goal or "").strip(),
        "prompt_tool_schemas_enabled": runner._prompt_tool_schemas_enabled,
        "runtime_tool_schemas": runtime_tool_schemas,
        "feasibility_sub_intents": [
            item.model_dump(mode="json") for item in structured_sub_intents
        ],
        "feasibility_plan_steps": [
            {
                "command_id": step.command_id,
                "kind": step.kind,
                "title": step.title,
                "tool_name": str(getattr(step, "tool_name", "") or "").strip(),
                "sub_intent_ids": list(getattr(step, "sub_intent_ids", []) or []),
                "success_criteria": dict(getattr(step, "success_criteria", {}) or {}),
            }
            for step in state.plan.steps
        ],
        "feasibility_runtime_facts": runtime_facts,
        "style_overrides": {
            "feasibility_contract": (
                "You are the feasibility gate. Use the declared sub-intents, plan steps, "
                "tool schemas, and runtime facts to determine what can execute before any "
                "tool runs. Return structured fields: plan_viable, recommendation "
                "(proceed_full|proceed_partial|retry_full|abort|suggest_alternatives), "
                "user_message, requires_user_choice, viable_intent_ids, blocked_intent_ids, "
                "and assessments[]. Each assessment must include intent_id, status "
                "(covered|partial|uncovered|unauthorized), reason, covering_tools, "
                "blocked_by, and alternatives. Tool availability is inferred from the "
                "provided tool schemas; runtime facts only add transient blocking details."
            )
        },
    }


def _validate_feasibility_report(
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    llm_call_id: str,
    report: FeasibilityReport,
    structured_sub_intents: list[Any],
) -> FeasibilityReport:
    allowed_ids = {item.id for item in structured_sub_intents}
    if allowed_ids:
        invalid_ids = (
            set(report.viable_intent_ids)
            | set(report.blocked_intent_ids)
            | {item.intent_id for item in report.assessments}
        ) - allowed_ids
        if invalid_ids:
            raise ValueError(
                "feasibility report referenced undeclared intent ids: "
                + ", ".join(sorted(invalid_ids))
            )
    logger.emit(
        "brain.feasibility.completed",
        {
            "llm_call_id": llm_call_id,
            "recommendation": report.recommendation,
            "plan_viable": report.plan_viable,
            "blocked_intent_ids": list(report.blocked_intent_ids),
        },
        trace_id=state.trace_id,
    )
    return report


def _fallback_feasibility_report(
    *, structured_sub_intents: list[Any]
) -> FeasibilityReport:
    declared_ids = [item.id for item in structured_sub_intents]
    return FeasibilityReport(
        plan_viable=False,
        recommendation="retry_full",
        user_message=(
            "I couldn't confidently determine which parts of this request are "
            "executable with the current tools. Reply 'retry' to try again, "
            "'continue' to proceed only if a viable subset exists, or 'cancel' to stop."
        ),
        requires_user_choice=True,
        viable_intent_ids=[],
        blocked_intent_ids=declared_ids,
        assessments=[],
    )


def _feasibility_barrel() -> Any:
    from ... import feasibility as feasibility_module

    return feasibility_module
