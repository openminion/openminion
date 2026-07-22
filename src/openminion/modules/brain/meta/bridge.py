from typing import TYPE_CHECKING

from .adapter import CheckpointAdapter
from .evaluator import MetaRulesEngine
from .metrics import build_meta_metrics
from .schemas import MetaDirective, MetaMetrics, MetaResult

from ..constants import (
    BRAIN_STATE_STOPPED,
    BRAIN_STATE_WAITING_USER,
    RESPOND_KIND_ASSISTANT,
    RespondKindLiteral,
)
from ..diagnostics.events import CanonicalEventLogger
from ..schemas import (
    ActionResult,
    BudgetCounters,
    Command,
    Decision,
    MetaDirectiveLogEntry,
    StepOutput,
    WorkingState,
)
from ..state import MetaApplication

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..runner import BrainRunner


def _evaluate_with_checkpoint(
    adapter: CheckpointAdapter,
    *,
    engine: MetaRulesEngine,
    hook: str,
    metrics: MetaMetrics,
) -> MetaResult:
    fn = getattr(adapter, hook, None)
    if not callable(fn):
        return engine.evaluate(metrics)
    return fn(metrics)


def _meta_override_for_hook(runner: "BrainRunner", hook: str) -> MetaResult | None:
    if runner._meta_overrides and hook in runner._meta_overrides:
        return runner._meta_overrides.pop(hook)
    return None


def _meta_is_disabled(runner: "BrainRunner", *, override: MetaResult | None) -> bool:
    return (
        not runner.options.metactl_enabled
        and override is None
        and runner.meta_api is None
        and runner.meta_engine is None
    )


def _budget_caps_for_runner(runner: "BrainRunner") -> BudgetCounters:
    return BudgetCounters(
        ticks=runner.profile.budgets.max_ticks_per_user_turn,
        tool_calls=runner.profile.budgets.max_tool_calls,
        a2a_calls=runner.profile.budgets.max_a2a_calls,
        tokens=runner.profile.budgets.max_total_llm_tokens,
        time_ms=runner.profile.budgets.max_elapsed_ms,
    )


def _resolve_meta_result(
    runner: "BrainRunner",
    *,
    hook: str,
    metrics: MetaMetrics,
    override: MetaResult | None,
) -> MetaResult:
    if override is not None:
        return override
    if runner.meta_api is not None:
        return runner.meta_api.evaluate(metrics)
    return _evaluate_with_checkpoint(
        CheckpointAdapter(runner.meta_engine),
        engine=runner.meta_engine,
        hook=hook,
        metrics=metrics,
    )


def _meta_metric_aliases(metrics: MetaMetrics) -> dict[str, float | int]:
    grounding_confidence = getattr(
        metrics, "grounding_confidence", getattr(metrics, "grounding_score", 1.0)
    )
    grounding_score = getattr(metrics, "grounding_score", grounding_confidence)
    recent_failures = getattr(
        metrics, "recent_failures", getattr(metrics, "repeat_error_count", 0)
    )
    loop_count = getattr(metrics, "loop_count", getattr(metrics, "ticks_without_progress", 0))
    replan_count = getattr(
        metrics, "replan_count", getattr(metrics, "no_new_facts_streak", 0)
    )
    budget_remaining = getattr(
        metrics, "budget_remaining", 1.0 - getattr(metrics, "budget_pressure", 0.0)
    )
    budget_pressure = getattr(metrics, "budget_pressure", 1.0 - budget_remaining)
    return {
        "grounding_confidence": grounding_confidence,
        "grounding_score": grounding_score,
        "recent_failures": recent_failures,
        "loop_count": loop_count,
        "replan_count": replan_count,
        "budget_remaining": budget_remaining,
        "budget_pressure": budget_pressure,
    }


def _meta_metrics_payload(
    *,
    metrics: MetaMetrics,
    result: MetaResult,
    hook: str,
    aliases: dict[str, float | int],
) -> dict:
    payload = metrics.model_dump(mode="json")
    payload["_telemetry_schema_version"] = "meta.metrics.v2"
    payload["grounding_score"] = aliases["grounding_score"]
    payload["repeat_error_count"] = aliases["recent_failures"]
    payload["ticks_without_progress"] = aliases["loop_count"]
    payload["no_new_facts_streak"] = aliases["replan_count"]
    payload["budget_pressure"] = aliases["budget_pressure"]
    payload["hook"] = hook
    payload["ruleset_version"] = result.ruleset_version
    return payload


def _meta_directive_payload(
    *,
    metrics: MetaMetrics,
    result: MetaResult,
    hook: str,
    aliases: dict[str, float | int],
) -> dict:
    return {
        "_telemetry_schema_version": "meta.directive.v2",
        "hook": hook,
        "meta_state": result.meta_state.value,
        "reasons": result.reasons,
        "ruleset_version": result.ruleset_version,
        "risk_score": metrics.risk_score,
        "grounding_confidence": aliases["grounding_confidence"],
        "grounding_score": aliases["grounding_score"],
        "progress": {
            "recent_failures": aliases["recent_failures"],
            "loop_count": aliases["loop_count"],
            "replan_count": aliases["replan_count"],
            "repeat_error_count": aliases["recent_failures"],
            "ticks_without_progress": aliases["loop_count"],
            "no_new_facts_streak": aliases["replan_count"],
        },
        "budget_remaining": aliases["budget_remaining"],
        "budget_pressure": aliases["budget_pressure"],
        "directive": result.directive.model_dump(mode="json"),
    }


def _emit_meta_telemetry(
    *,
    logger: CanonicalEventLogger,
    state: WorkingState,
    hook: str,
    metrics: MetaMetrics,
    result: MetaResult,
) -> None:
    aliases = _meta_metric_aliases(metrics)
    logger.emit(
        "meta.metrics",
        _meta_metrics_payload(metrics=metrics, result=result, hook=hook, aliases=aliases),
        trace_id=state.trace_id,
    )
    logger.emit(
        "meta.directive",
        _meta_directive_payload(metrics=metrics, result=result, hook=hook, aliases=aliases),
        trace_id=state.trace_id,
    )
    logger.emit(
        "policy.evaluated",
        {
            "hook": hook,
            "outcome": result.meta_state.value,
            "reason": result.reasons[0] if result.reasons else "standard evaluation",
            "ruleset_version": result.ruleset_version,
        },
        trace_id=state.trace_id,
    )


def evaluate_meta(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    hook: str,
    user_input: str | None = None,
    user_feedback_flags: dict[str, bool] | None = None,
    decision: Decision | None = None,
    command: Command | None = None,
    action_result: ActionResult | None = None,
) -> MetaResult | None:
    override = _meta_override_for_hook(runner, hook)
    if _meta_is_disabled(runner, override=override):
        return None

    metrics = build_meta_metrics(
        state=state,
        budget_caps=_budget_caps_for_runner(runner),
        decision=decision,
        command=command,
        action_result=action_result,
        user_input=user_input,
        user_feedback_flags=user_feedback_flags,
        cfg=runner.options.metactl_config,
    )
    result = _resolve_meta_result(runner, hook=hook, metrics=metrics, override=override)
    state.meta_state = result.meta_state.value
    _emit_meta_telemetry(
        logger=logger,
        state=state,
        hook=hook,
        metrics=metrics,
        result=result,
    )
    return result


def apply_meta_directive(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    directive: MetaDirective,
    logger: CanonicalEventLogger,
    hook: str,
    meta_state: str | None = None,
) -> None:
    application = MetaApplication(
        tier_before=state.tier,
        tier_after=state.tier,
        constraints_added=[],
        budgets_adjusted=False,
        llm_calls_max_before=state.llm_calls_max,
        llm_calls_max_after=state.llm_calls_max,
    )

    if directive.tier_override is not None:
        state.tier = directive.tier_override

    for constraint in directive.prompt_constraints:
        if constraint not in state.constraints:
            state.constraints.append(constraint)
            application.constraints_added.append(constraint)

    adjust = getattr(directive, "budget_adjustments", None)
    if adjust is None:
        adjust = getattr(directive, "budget_adjust", None)
    if adjust is not None:
        if adjust.lower_context_limits:
            cap = max(1, int(runner.profile.budgets.max_total_llm_tokens * 0.8))
            state.budgets_remaining.tokens = min(state.budgets_remaining.tokens, cap)
            application.budgets_adjusted = True
        if adjust.raise_context_limits:
            cap = runner.profile.budgets.max_total_llm_tokens
            state.budgets_remaining.tokens = min(
                cap, max(state.budgets_remaining.tokens, int(cap * 0.9))
            )
            application.budgets_adjusted = True
        if adjust.lower_llm_calls_max is not None:
            state.llm_calls_max = max(
                1, min(state.llm_calls_max, int(adjust.lower_llm_calls_max))
            )
            application.budgets_adjusted = True
        if adjust.raise_llm_calls_max is not None:
            state.llm_calls_max = max(
                state.llm_calls_max, int(adjust.raise_llm_calls_max)
            )
            application.budgets_adjusted = True

    application.tier_after = state.tier
    application.llm_calls_max_after = state.llm_calls_max
    runner._last_meta_application = application

    payload = {
        "hook": hook,
        "meta_state": meta_state or state.meta_state,
        "tier_before": application.tier_before,
        "tier_after": application.tier_after,
        "constraints_added": application.constraints_added,
        "budgets_adjusted": application.budgets_adjusted,
        "llm_calls_max_before": application.llm_calls_max_before,
        "llm_calls_max_after": application.llm_calls_max_after,
        "directive": directive.model_dump(mode="json"),
    }
    logger.emit("brain.meta_applied", payload, trace_id=state.trace_id)
    state.meta_logs.append(
        MetaDirectiveLogEntry(
            hook=hook,
            meta_state=payload["meta_state"],
            directive=payload["directive"],
        )
    )


def meta_override_response(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    directive: MetaDirective,
    fallback_message: str,
    action_result: ActionResult | None = None,
) -> StepOutput | None:
    if bool(directive.require_clarification):
        return respond_with_meta(
            runner,
            state=state,
            logger=logger,
            message=(
                directive.clarification_question
                or directive.escalation_question
                or directive.note_to_user
                or fallback_message
            ),
            status=BRAIN_STATE_WAITING_USER,
            action_result=action_result,
        )

    override_next_state = directive.override_next_state
    if override_next_state is None:
        return None

    message = fallback_message
    status = BRAIN_STATE_WAITING_USER
    if override_next_state == "WAITING":
        message = (
            directive.escalation_question or directive.note_to_user or fallback_message
        )
    elif override_next_state == "RESPOND":
        message = directive.note_to_user or fallback_message
    elif override_next_state == "STOPPED":
        status = BRAIN_STATE_STOPPED
        message = (
            directive.note_to_user
            or "Safety stop enabled. No actions will be executed."
        )
    else:
        return None

    return respond_with_meta(
        runner,
        state=state,
        logger=logger,
        message=message,
        status=status,
        action_result=action_result,
    )


def respond_with_meta(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    message: str,
    status: str,
    action_result: ActionResult | None = None,
    kind: RespondKindLiteral = RESPOND_KIND_ASSISTANT,
) -> StepOutput:
    final_message = message
    final_status = status

    meta_before_respond = evaluate_meta(
        runner,
        state=state,
        logger=logger,
        hook="before_respond",
        action_result=action_result,
    )
    if meta_before_respond is not None:
        directive = meta_before_respond.directive
        apply_meta_directive(
            runner,
            state=state,
            directive=directive,
            logger=logger,
            hook="before_respond",
            meta_state=meta_before_respond.meta_state.value,
        )
        if directive.override_next_state == "STOPPED":
            final_status = BRAIN_STATE_STOPPED
            final_message = (
                directive.note_to_user
                or "Safety stop enabled. No actions will be executed."
            )
        elif (
            status != BRAIN_STATE_STOPPED and directive.override_next_state == "WAITING"
        ):
            final_status = BRAIN_STATE_WAITING_USER
            final_message = (
                directive.escalation_question or directive.note_to_user or final_message
            )
        elif (
            status != BRAIN_STATE_STOPPED
            and directive.override_next_state == "RESPOND"
            and directive.note_to_user
        ):
            final_message = directive.note_to_user
        elif directive.note_to_user and directive.note_to_user not in final_message:
            final_message = f"{directive.note_to_user}\n{final_message}"

    if status == BRAIN_STATE_STOPPED:
        final_status = BRAIN_STATE_STOPPED

    return runner._respond(
        state=state,
        logger=logger,
        message=final_message,
        status=final_status,
        action_result=action_result,
        kind=kind,
    )


def meta_tool_restriction_reason(
    *, command: Command, directive: MetaDirective
) -> str | None:
    if command.kind != "tool":
        return None
    tool_name = command.tool_name
    deny = set(directive.tool_temp_denylist)
    allow = set(directive.tool_temp_allowlist)

    if "*" in deny or tool_name in deny:
        return f"Meta governor temporarily blocked tool '{tool_name}'."
    if allow and "*" not in allow and tool_name not in allow:
        allow_text = ", ".join(sorted(allow))
        return f"Meta governor temporarily allows only: {allow_text}."
    return None
