from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from ..runtime.context import build_context as build_context_pack
from ..bootstrap.skill.hints import resolve_skill_hints as resolve_skill_hints_events
from ..execution.failure_response import (
    build_time_sensitive_failure_response as build_time_sensitive_failure_response_events,
)
from ..execution.tool_inventory import (
    build_tool_inventory_response as build_tool_inventory_response_events,
)
from ..execution import (
    advance_after_action as advance_after_action_exec,
    apply_closure_judgment as apply_closure_judgment_exec,
    available_tool_names as available_tool_names_exec,
    evaluate_post_action_judgment as evaluate_post_action_judgment_exec,
    budget_blocked_result as budget_blocked_result_exec,
    build_forced_tool_command as build_forced_tool_command_exec,
    evaluate_turn_closure as evaluate_turn_closure_exec,
    normalize_execution_result as normalize_execution_result_exec,
    reconcile_pending_jobs as reconcile_pending_jobs_exec,
    remember_idempotency as remember_idempotency_exec,
    resolve_browser_tool as resolve_browser_tool_exec,
    resolve_capability_tool_fallback as resolve_capability_tool_fallback_exec,
    resolve_forced_tool_name as resolve_forced_tool_name_exec,
    run_recursive_turn as run_recursive_turn_exec,
    validate_tool_args as validate_tool_args_exec,
)
from ..execution.feasibility import assess_plan_feasibility
from ..loop.clarify import (
    clarify as clarify_flow,
    enter_clarify_mode as enter_clarify_mode_flow,
    process_clarification_response as process_clarification_response_flow,
)
from ..loop.orchestration import decide as decide_flow
from ..bootstrap.recovery import heuristic_decision as heuristic_decision_flow
from ..bootstrap.payloads import (
    normalize_decision_payload as normalize_decision_payload_flow,
)
from ..runtime.memory import apply_improvements
from ..meta.bridge import (
    apply_meta_directive as apply_meta_directive_flow,
    evaluate_meta as evaluate_meta_flow,
    meta_override_response as meta_override_response_flow,
    meta_tool_restriction_reason as meta_tool_restriction_reason_flow,
    respond_with_meta as respond_with_meta_flow,
)
from ..runtime.verification.policy import (
    resolve_verification_mode,
    verify as verify_policy,
)
from .turn import (
    autonomous_requires_confirmation as autonomous_requires_confirmation_utils,
    command_has_side_effects as command_has_side_effects_utils,
    debit_tokens as debit_tokens_runner_logging,
    direct_response as direct_response_utils,
    estimate_tokens as estimate_tokens_runner_logging,
    idempotency_key as idempotency_key_utils,
    interpret as interpret_runner_utils,
    is_time_sensitive_tool_command as is_time_sensitive_tool_command_utils,
    now_ms as now_ms_utils,
    track_call_completed as track_call_completed_runner_logging,
    track_call_started as track_call_started_runner_logging,
    validate_call_order as validate_call_order_runner_logging,
)
from ..schemas import (
    ActionResult,
    AskUserCommand,
    Command,
    JobHandle,
    PolicyDecision,
    WorkingState,
)
from ..constants import BRAIN_COMMAND_KIND_ASK_USER
from ..tools.executor import execute_action
from openminion.modules.tool.contracts.model_ids import (  # noqa: PLC0415
    MODEL_TASK_CANCEL,
    MODEL_TASK_SCHEDULE,
)
from ..state import (
    compact as compact_state,
    consume_tick as consume_tick_state,
    load_or_init_state,
    respond as respond_state,
    save_state,
)
from ..tools.parser import (
    normalize_command_payload,
    parse_agent_command,
    parse_tool_command,
)
from ..tools.schema import (
    build_prompt_tool_schemas,
    collect_runtime_tool_schemas,
    tool_description,
    tool_parameters,
)


def _delegate_without_runner(func: Callable[..., Any]) -> Callable[..., Any]:
    def _delegate(_runner: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, **kwargs)

    return _delegate


def _delegate_with_runner_keyword(func: Callable[..., Any]) -> Callable[..., Any]:
    def _delegate(runner: Any, *args: Any, **kwargs: Any) -> Any:
        return func(*args, runner=runner, **kwargs)

    return _delegate


def _normalize_decision_payload_delegate(runner: Any, raw: Any) -> Any:
    return normalize_decision_payload_flow(runner=runner, raw=raw)


def _is_time_sensitive_tool_command_delegate(runner: Any, command: Command) -> bool:
    return is_time_sensitive_tool_command_utils(runner, command=command)


def _evaluate_post_action_judgment_delegate(
    runner: Any,
    *,
    state: WorkingState,
    logger,
    fact_kind: str,
    action_result: ActionResult | None = None,
    current_command: Command | None = None,
    current_step_index: int | None = None,
    total_steps: int | None = None,
    runtime_facts: dict[str, Any] | None = None,
):
    return evaluate_post_action_judgment_exec(
        runner,
        state=state,
        logger=logger,
        fact_kind=fact_kind,
        action_result=action_result,
        current_command=current_command,
        current_step_index=current_step_index,
        total_steps=total_steps,
        runtime_facts=runtime_facts,
    )


def _format_confirmation_value(value: Any) -> str:
    text = str(value).replace("\n", " ").strip()
    if len(text) > 180:
        return f"{text[:177]}..."
    return text


def _format_every_schedule(every_ms: int) -> str:
    if every_ms % 86_400_000 == 0:
        days = every_ms // 86_400_000
        return f"every {days} day{'s' if days != 1 else ''}"
    if every_ms % 3_600_000 == 0:
        hours = every_ms // 3_600_000
        return f"every {hours} hour{'s' if hours != 1 else ''}"
    if every_ms % 60_000 == 0:
        minutes = every_ms // 60_000
        return f"every {minutes} minute{'s' if minutes != 1 else ''}"
    if every_ms % 1_000 == 0:
        seconds = every_ms // 1_000
        return f"every {seconds} second{'s' if seconds != 1 else ''}"
    return f"every {every_ms}ms"


def _render_schedule_for_confirmation(schedule: Any) -> str:
    if not isinstance(schedule, Mapping):
        return _format_confirmation_value(schedule)
    kind = str(schedule.get("kind") or "").strip().lower()
    if kind == "every":
        every_ms = int(schedule.get("every_ms", 0) or 0)
        if every_ms > 0:
            return _format_every_schedule(every_ms)
    if kind == "cron":
        expr = str(schedule.get("expr") or "").strip()
        tz_name = str(schedule.get("tz") or "UTC").strip()
        if expr:
            return f"cron {expr} ({tz_name})"
    if kind == "at":
        at_value = str(schedule.get("at") or "").strip()
        if at_value:
            return f"at {at_value}"
    return _format_confirmation_value(schedule)


def _build_confirmation_question(*, command: Command, fallback: str) -> str:
    if getattr(command, "kind", None) != "tool":
        return fallback
    tool_name = str(getattr(command, "tool_name", "") or "").strip()
    args_raw = getattr(command, "args", {})
    args = args_raw if isinstance(args_raw, Mapping) else {}
    if not tool_name:
        return fallback

    details: list[str] = []
    if tool_name == MODEL_TASK_SCHEDULE:
        instruction = str(args.get("instruction") or "").strip()
        schedule_text = _render_schedule_for_confirmation(args.get("schedule"))
        task_name = str(args.get("name") or "").strip()
        task_id = str(args.get("task_id") or "").strip()
        if instruction:
            details.append(
                f'- instruction: "{_format_confirmation_value(instruction)}"'
            )
        if schedule_text:
            details.append(f"- schedule: {schedule_text}")
        if task_name:
            details.append(f'- name: "{_format_confirmation_value(task_name)}"')
        if task_id:
            details.append(f'- task_id: "{_format_confirmation_value(task_id)}"')
    elif tool_name == MODEL_TASK_CANCEL:
        task_id = str(args.get("task_id") or "").strip()
        if task_id:
            details.append(f'- task_id: "{_format_confirmation_value(task_id)}"')

    if not details:
        return fallback

    header = f"{tool_name} will be called with:"
    prefix = fallback or "Policy requires confirmation before proceeding."
    return f"{prefix}\n\n{header}\n" + "\n".join(details)


def _act_delegate(
    runner: Any,
    *,
    state: WorkingState,
    command: Command,
    logger: Any,
) -> tuple[ActionResult, JobHandle | None]:
    return execute_action(runner, state=state, command=command, logger=logger)


def _approve_delegate(
    runner: Any,
    *,
    state: WorkingState,
    command: Command,
    logger: Any,
) -> Command:
    session_context: dict[str, Any] = {
        "session_id": state.session_id,
        "trace_id": state.trace_id,
        "constraints": state.constraints,
        "mode_name": getattr(state, "active_mode_name", None),
    }
    if runner.policy_api is None:
        decision: PolicyDecision = PolicyDecision(
            outcome="ALLOW", explanation="No policy engine configured."
        )
    else:
        decision = runner.policy_api.evaluate(
            command=command,
            working_state=state,
            session_context=session_context,
        )

    payload: dict[str, Any] = {
        "outcome": decision.outcome,
        "explanation": decision.explanation,
        "require_clarification": bool(decision.require_clarification),
    }
    if decision.clarification_question:
        payload["clarification_question"] = decision.clarification_question
    if decision.patched_command is not None:
        payload["patched_command"] = decision.patched_command.model_dump(mode="json")
    logger.emit("policy.applied", payload, trace_id=state.trace_id)

    if decision.outcome == "REQUIRE_CLARIFICATION" or bool(
        decision.require_clarification
    ):
        return AskUserCommand(
            kind=BRAIN_COMMAND_KIND_ASK_USER,
            title="Policy clarification required",
            question=decision.clarification_question
            or decision.explanation
            or "Please provide clarification before proceeding.",
            success_criteria={"clarification_received": True},
        )
    if decision.outcome == "DENY":
        return AskUserCommand(
            kind=BRAIN_COMMAND_KIND_ASK_USER,
            title="Policy denied",
            question=decision.explanation or "Policy denied this action.",
            success_criteria={"user_acknowledged": True},
        )
    if decision.outcome == "ALLOW":
        return command
    if decision.outcome == "MODIFY":
        return decision.patched_command or command
    fallback_question = (
        decision.explanation
        or "Policy requires confirmation before proceeding. Proceed?"
    )
    confirmation_question = _build_confirmation_question(
        command=command,
        fallback=fallback_question,
    )
    return AskUserCommand(
        kind=BRAIN_COMMAND_KIND_ASK_USER,
        title="Policy confirmation required",
        question=confirmation_question,
        options=["Yes", "No"],
        success_criteria={"confirmation_received": True},
    )


RUNNER_DELEGATES: dict[str, Callable[..., Any]] = {
    "_run_recursive_turn": run_recursive_turn_exec,
    "_load_or_init_state": load_or_init_state,
    "_save_state": save_state,
    "_interpret": interpret_runner_utils,
    "_collect_runtime_tool_schemas": collect_runtime_tool_schemas,
    "_build_prompt_tool_schemas": build_prompt_tool_schemas,
    "_tool_description": _delegate_without_runner(tool_description),
    "_tool_parameters": _delegate_without_runner(tool_parameters),
    "_normalize_command_payload": _delegate_without_runner(normalize_command_payload),
    "_normalize_decision_payload": _normalize_decision_payload_delegate,
    "_autonomous_requires_confirmation": _delegate_without_runner(
        autonomous_requires_confirmation_utils
    ),
    "_approve": _approve_delegate,
    "_act": _act_delegate,
    "_clarify": clarify_flow,
    "_decide": decide_flow,
    "_heuristic_decision": heuristic_decision_flow,
    "_is_time_sensitive_tool_command": _is_time_sensitive_tool_command_delegate,
    "_build_time_sensitive_failure_response": build_time_sensitive_failure_response_events,
    "_build_tool_inventory_response": build_tool_inventory_response_events,
    "_improve": apply_improvements,
    "_compact": compact_state,
    "_advance_after_action": advance_after_action_exec,
    "_evaluate_post_action_judgment": _evaluate_post_action_judgment_delegate,
    "_evaluate_turn_closure": evaluate_turn_closure_exec,
    "_apply_closure_judgment": apply_closure_judgment_exec,
    "_assess_plan_feasibility": assess_plan_feasibility,
    "_evaluate_meta": evaluate_meta_flow,
    "_apply_meta_directive": apply_meta_directive_flow,
    "_meta_override_response": meta_override_response_flow,
    "_respond_with_meta": respond_with_meta_flow,
    "_command_has_side_effects": command_has_side_effects_utils,
    "_meta_tool_restriction_reason": _delegate_without_runner(
        meta_tool_restriction_reason_flow
    ),
    "_resolve_verification_mode": _delegate_without_runner(resolve_verification_mode),
    "_resolve_skill_hints": resolve_skill_hints_events,
    "_verify": _delegate_without_runner(verify_policy),
    "_respond": respond_state,
    "_direct_response": _delegate_without_runner(direct_response_utils),
    "_consume_tick": _delegate_without_runner(consume_tick_state),
    "_reconcile_pending_jobs": reconcile_pending_jobs_exec,
    "_build_context": build_context_pack,
    "_track_call_started": track_call_started_runner_logging,
    "_track_call_completed": track_call_completed_runner_logging,
    "_validate_call_order": validate_call_order_runner_logging,
    "_estimate_tokens": estimate_tokens_runner_logging,
    "_debit_tokens": _delegate_without_runner(debit_tokens_runner_logging),
    "_remember_idempotency": remember_idempotency_exec,
    "_validate_tool_args": validate_tool_args_exec,
    "_normalize_execution_result": _delegate_without_runner(
        normalize_execution_result_exec
    ),
    "_budget_blocked_result": _delegate_without_runner(budget_blocked_result_exec),
    "_resolve_forced_tool_name": resolve_forced_tool_name_exec,
    "_resolve_capability_tool_fallback": _delegate_without_runner(
        resolve_capability_tool_fallback_exec
    ),
    "_build_forced_tool_command": build_forced_tool_command_exec,
    "_parse_tool_command": _delegate_with_runner_keyword(parse_tool_command),
    "_parse_agent_command": _delegate_with_runner_keyword(parse_agent_command),
    "_idempotency_key": _delegate_without_runner(idempotency_key_utils),
    "_available_tool_names": available_tool_names_exec,
    "_resolve_browser_tool": resolve_browser_tool_exec,
    "_now_ms": _delegate_without_runner(now_ms_utils),
    "_process_clarification_response": process_clarification_response_flow,
    "_enter_clarify_mode": enter_clarify_mode_flow,
}
