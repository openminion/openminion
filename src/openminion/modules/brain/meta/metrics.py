from typing import Literal

from .evaluator import MetaRulesEngine
from .schemas import (
    BudgetAdjust,
    MetaConfig,
    MetaDirective,
    MetaMetrics,
    MetaResult,
    MetaState,
    VerificationMode,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_EXEC_RUN,
    MODEL_FILE_FIND,
    MODEL_FILE_LIST_DIR,
    MODEL_FILE_READ,
    MODEL_TIME,
    MODEL_WEATHER,
    MODEL_WEB_SEARCH,
)
from openminion.tools.exec.command_parser import is_read_only_exec_command
from openminion.tools.exec.process import resolve_shell_family

from ..schemas import (
    ActionResult,
    BudgetCounters,
    Command,
    Decision,
    WorkingState,
)
from ..constants import (
    BRAIN_ACTION_STATUS_BLOCKED,
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_RETRY,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_ACTION_STATUS_TIMEOUT,
    BRAIN_COMMAND_KIND_AGENT,
    BRAIN_COMMAND_KIND_TOOL,
    BRAIN_DECISION_ROUTE_RESPOND,
    BRAIN_RESPOND_KIND_CLARIFY,
)

__all__ = [
    "BudgetAdjust",
    "MetaConfig",
    "MetaDirective",
    "MetaMetrics",
    "MetaResult",
    "MetaRulesEngine",
    "MetaState",
    "VerificationMode",
    "build_meta_metrics",
]


def build_meta_metrics(
    *,
    state: WorkingState,
    budget_caps: BudgetCounters,
    decision: Decision | None = None,
    command: Command | None = None,
    action_result: ActionResult | None = None,
    user_input: str | None = None,
    user_feedback_flags: dict[str, bool] | None = None,
    cfg: MetaConfig | None = None,
) -> MetaMetrics:
    cfg = cfg or MetaConfig()
    current_command = command or _current_command(state)
    feedback = _derive_user_feedback_flags(
        user_input=user_input, explicit=user_feedback_flags
    )

    # LLM respond.clarify is conversational clarification and must pass through.
    # Runtime clarification signal should only reflect pending runtime clarification items.
    needs_clarification = bool(
        getattr(state, "unresolved_clarify_items", None)
        or getattr(state, "pending_clarify_items", None)
    )
    ambiguity_score = 0.0
    if (
        decision is not None
        and decision.route == BRAIN_DECISION_ROUTE_RESPOND
        and str(getattr(decision, "respond_kind", "") or "").strip()
        == BRAIN_RESPOND_KIND_CLARIFY
    ):
        ambiguity_score = max(ambiguity_score, 0.7)

    unknown_fields_count = 0
    if needs_clarification:
        unknown_fields_count = 1

    intent_confidence = decision.confidence if decision is not None else 0.7
    if needs_clarification:
        intent_confidence = min(intent_confidence, 0.55)

    side_effects_pending = _command_has_side_effects(current_command)
    irreversible_action_pending = _command_is_irreversible(current_command)
    risk_score = _risk_score_for_command(current_command)
    if side_effects_pending:
        risk_score += 5
    if irreversible_action_pending:
        risk_score += 15
    risk_score = max(0, min(100, risk_score))

    risk_class: Literal["low", "medium", "high"] = "low"
    if risk_score >= cfg.high_risk_score_threshold:
        risk_class = "high"
    elif risk_score >= 40:
        risk_class = "medium"

    repeat_error_count = max(state.retries_for_step.values(), default=0)
    ticks_without_progress = sum(state.retries_for_step.values())
    no_new_facts_streak = 0
    target_result = action_result or state.last_result
    if target_result is not None and not _result_has_facts(target_result):
        no_new_facts_streak = max(1, repeat_error_count)
    if target_result is not None and target_result.status in {
        BRAIN_ACTION_STATUS_FAILED,
        BRAIN_ACTION_STATUS_RETRY,
        BRAIN_ACTION_STATUS_TIMEOUT,
    }:
        repeat_error_count = max(repeat_error_count, 1)

    grounding_score = _grounding_score_for_result(target_result)
    contradiction_flags: list[str] = []
    candidate_disagreement_score = 0.0
    requires_evidence_only = grounding_score < cfg.low_grounding_threshold

    tool_success_rate_ewma = _tool_health_score(target_result)
    tool_timeout_count_recent = (
        1
        if target_result is not None
        and target_result.status == BRAIN_ACTION_STATUS_TIMEOUT
        else 0
    )
    tool_auth_error_count_recent = 0
    if (
        target_result is not None
        and target_result.error is not None
        and "AUTH" in target_result.error.code.upper()
    ):
        tool_auth_error_count_recent = 1

    llm_calls_used = getattr(state, "llm_calls_used", 0)
    llm_calls_max = max(1, getattr(state, "llm_calls_max", 8))
    tool_calls_used = max(
        0, budget_caps.tool_calls - state.budgets_remaining.tool_calls
    )
    tool_calls_max = max(1, budget_caps.tool_calls)
    budget_pressure = _max_ratio(
        _ratio(budget_caps.ticks - state.budgets_remaining.ticks, budget_caps.ticks),
        _ratio(
            budget_caps.tool_calls - state.budgets_remaining.tool_calls,
            budget_caps.tool_calls,
        ),
        _ratio(
            budget_caps.a2a_calls - state.budgets_remaining.a2a_calls,
            budget_caps.a2a_calls,
        ),
        _ratio(budget_caps.tokens - state.budgets_remaining.tokens, budget_caps.tokens),
        _ratio(
            budget_caps.time_ms - state.budgets_remaining.time_ms, budget_caps.time_ms
        ),
        _ratio(llm_calls_used, llm_calls_max),
    )

    return MetaMetrics(
        session_id=state.session_id,
        agent_id=state.agent_id,
        trace_id=state.trace_id or "",
        state=state.status,
        planned_next_state=current_command.kind.upper()
        if current_command is not None
        else "",
        tier=state.tier,
        intent_confidence=intent_confidence,
        unknown_fields_count=unknown_fields_count,
        ambiguity_score=ambiguity_score,
        needs_clarification=needs_clarification,
        risk_score=risk_score,
        risk_class=risk_class,
        irreversible=irreversible_action_pending,
        requires_side_effects=side_effects_pending,
        steps_completed_recent=state.cursor,
        loop_count=ticks_without_progress,
        recent_failures=repeat_error_count,
        replan_count=no_new_facts_streak,
        ticks_without_progress=ticks_without_progress,
        no_new_facts_streak=no_new_facts_streak,
        grounding_confidence=grounding_score,
        contradiction_flags=contradiction_flags,
        candidate_disagreement_score=candidate_disagreement_score,
        requires_evidence_only=requires_evidence_only,
        tool_success_rate_ewma=tool_success_rate_ewma,
        tool_timeout_count_recent=tool_timeout_count_recent,
        tool_auth_error_count_recent=tool_auth_error_count_recent,
        llm_calls_used=llm_calls_used,
        llm_calls_max=llm_calls_max,
        tool_calls_used=tool_calls_used,
        tool_calls_max=tool_calls_max,
        budget_remaining=max(0.0, 1.0 - budget_pressure),
        budget_pressure=budget_pressure,
        user_corrected_me_recently=feedback.get("user_corrected_me_recently", False),
        user_requested_thoroughness=feedback.get("user_requested_thoroughness", False),
        user_requested_brevity=feedback.get("user_requested_brevity", False),
        user_kill_requested=feedback.get("user_kill_requested", False),
    )


def _current_command(state: WorkingState) -> Command | None:
    if state.plan is None:
        return None
    if state.cursor >= len(state.plan.steps):
        return None
    return state.plan.steps[state.cursor]


def _risk_score_for_command(command: Command | None) -> int:
    if command is None:
        return 10
    risk_map = {"low": 20, "med": 55, "high": 85}
    return int(risk_map.get(getattr(command, "risk_level", "low"), 20))


def _command_has_side_effects(command: Command | None) -> bool:
    if command is None:
        return False
    if command.kind == BRAIN_COMMAND_KIND_AGENT:
        return True
    if command.kind != BRAIN_COMMAND_KIND_TOOL:
        return False

    tool_name = str(getattr(command, "tool_name", "")).strip().lower()
    read_only_tools = {
        MODEL_FILE_LIST_DIR,
        MODEL_FILE_READ,
        MODEL_FILE_FIND,
        MODEL_WEB_SEARCH,
        MODEL_WEATHER,
        MODEL_TIME,
    }
    if tool_name in read_only_tools:
        return False

    if tool_name == MODEL_EXEC_RUN:
        args = getattr(command, "args", {}) or {}
        raw_command = (
            str(args.get("command", "")).strip() if isinstance(args, dict) else ""
        )
        if is_read_only_exec_command(
            raw_command,
            shell_family=resolve_shell_family(),
        ):
            return False
        return True

    return True


def _command_is_irreversible(command: Command | None) -> bool:
    """Return True for narrow irreversible-action safety latches."""
    if command is None:
        return False
    if getattr(command, "risk_level", "low") == "high":
        return True
    if command.kind != "tool":
        return False
    tool_name = getattr(command, "tool_name", "").lower()
    dangerous = {
        "rm",
        "delete",
        "drop",
        "destroy",
        "shutdown",
        "reboot",
        "format",
        "wipe",
        "truncate",
    }
    if tool_name in dangerous:
        return True
    args = getattr(command, "args", {})
    if isinstance(args, dict):
        for key in ("force", "recursive", "permanent", "delete"):
            if bool(args.get(key)):
                return True
    return False


def _result_has_facts(result: ActionResult) -> bool:
    if result.artifact_refs or result.memory_refs:
        return True
    return bool(result.outputs)


def _grounding_score_for_result(result: ActionResult | None) -> float:
    if result is None:
        return 1.0
    if result.status == BRAIN_ACTION_STATUS_SUCCESS:
        if result.artifact_refs or result.memory_refs:
            return 1.0
        if result.outputs:
            return 0.9
        return 0.7
    if result.status in {BRAIN_ACTION_STATUS_BLOCKED, BRAIN_ACTION_STATUS_NEEDS_USER}:
        return 0.65
    if result.error is not None:
        return 0.35
    return 0.5


def _tool_health_score(result: ActionResult | None) -> float:
    if result is None:
        return 1.0
    if result.status == BRAIN_ACTION_STATUS_SUCCESS:
        return 1.0
    if result.status in {BRAIN_ACTION_STATUS_RETRY, BRAIN_ACTION_STATUS_NEEDS_USER}:
        return 0.75
    if result.status == BRAIN_ACTION_STATUS_BLOCKED:
        return 0.7
    return 0.4


def _ratio(used: int, max_value: int) -> float:
    if max_value <= 0:
        return 0.0
    return max(0.0, min(1.0, float(used) / float(max_value)))


def _max_ratio(*values: float) -> float:
    if not values:
        return 0.0
    return max(values)


def _derive_user_feedback_flags(
    user_input: str | None, explicit: dict[str, bool] | None
) -> dict[str, bool]:
    flags = {
        "user_corrected_me_recently": False,
        "user_requested_thoroughness": False,
        "user_requested_brevity": False,
        "user_kill_requested": False,
    }
    if explicit:
        for key, value in explicit.items():
            if key in flags:
                flags[key] = bool(value)

    text = (user_input or "").lower().strip()
    if not text:
        return flags

    if any(
        token in text
        for token in ("kill switch", "panic stop", "emergency stop", "stop all actions")
    ):
        flags["user_kill_requested"] = True

    return flags
