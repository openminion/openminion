from dataclasses import dataclass
from typing import Any

from ..constants import (
    BRAIN_DECISION_ROUTE_ACT,
)
from ..diagnostics.events import CanonicalEventLogger
from ..schemas.decisions import GoalDeclaration, GoalRevision, MetaRulePreference
from ..schemas.readiness import (
    command_payload_prefix,
    payload_is_contextually_empty,
    validate_command_readiness,
)
from ..runtime.memory import (
    stage_declared_goal,
    stage_goal_revision,
    stage_meta_rule_preference,
)
from ..bootstrap import resolve as _resolve_barrel
from . import dispatch as _dispatch_barrel
from . import memory as _memory_barrel
from .dispatch import _decision_route_name
from .runtime.turn.dispatch import dispatch as _dispatch_impl
from .preflight import ValidationResult

prepare_decision_direct = _dispatch_barrel.prepare_decision_direct
validate_decision_direct = _dispatch_barrel.validate_decision_direct
invoke_decision_direct = _dispatch_barrel.invoke_decision_direct
resolve_working_act_route = _resolve_barrel.resolve_working_act_route
apply_resolved_act_route = _resolve_barrel.apply_resolved_act_route
write_decision_memory = _memory_barrel.write_decision_memory


@dataclass(slots=True)
class ExecutionEntryRequest:
    user_input: str | None = None
    forced_tools: list[str] | None = None
    capability_category: str | None = None
    skip_decide: bool = False
    decision: Any | None = None
    mask_pending_confirmation_in_output: bool = False
    masked_resume_cursor: int | None = None
    consume_user_input_for_command: bool = False


def _copied_seeded_commands(commands: list[Any]) -> list[Any]:
    return [command.model_copy(deep=True) for command in list(commands or [])]


def _seeded_sub_intent_ids(commands: list[Any]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for command in list(commands or []):
        for raw_intent_id in list(getattr(command, "sub_intent_ids", []) or []):
            intent_id = str(raw_intent_id or "").strip()
            if not intent_id or intent_id in seen:
                continue
            seen.add(intent_id)
            ordered.append(intent_id)
    return ordered


def _sync_typed_decision_signals(
    *,
    runner: Any,
    state: Any,
    decision: Any,
) -> None:
    summary = getattr(decision, "session_work_summary", None)
    summary_text = str(getattr(summary, "summary", "") or "").strip()
    if summary_text:
        state.session_work_summary = summary_text

    preference = getattr(decision, "meta_rule_preference", None)
    if preference is not None and getattr(runner, "memory_api", None) is not None:
        stage_meta_rule_preference(
            runner,
            state=state,
            preference=MetaRulePreference.model_validate(preference),
        )

    # stage `declared_goal` candidate from the single-decision
    goal = getattr(decision, "goal_declaration", None)
    if goal is not None and getattr(runner, "memory_api", None) is not None:
        try:
            staged = (
                goal
                if isinstance(goal, GoalDeclaration)
                else GoalDeclaration.model_validate(goal)
            )
        except Exception:
            staged = None
        if staged is not None:
            stage_declared_goal(
                runner,
                state=state,
                goal=staged,
            )
    revision = getattr(decision, "goal_revision", None)
    if revision is not None and getattr(runner, "memory_api", None) is not None:
        try:
            staged_revision = (
                revision
                if isinstance(revision, GoalRevision)
                else GoalRevision.model_validate(revision)
            )
        except Exception:
            staged_revision = None
        if staged_revision is not None:
            stage_goal_revision(
                runner,
                state=state,
                goal_revision=staged_revision,
            )


def build_execution_entry_request(
    *,
    user_input: str | None,
    forced_tools: list[str] | None,
    capability_category: str | None,
    skip_decide: bool = False,
    decision: Any | None = None,
    mask_pending_confirmation_in_output: bool = False,
    masked_resume_cursor: int | None = None,
    consume_user_input_for_command: bool = False,
) -> ExecutionEntryRequest:
    return ExecutionEntryRequest(
        user_input=user_input,
        forced_tools=forced_tools,
        capability_category=capability_category,
        skip_decide=skip_decide,
        decision=decision,
        mask_pending_confirmation_in_output=mask_pending_confirmation_in_output,
        masked_resume_cursor=masked_resume_cursor,
        consume_user_input_for_command=consume_user_input_for_command,
    )


def dispatch(
    *,
    runner: Any,
    state: Any,
    logger: CanonicalEventLogger,
    request: ExecutionEntryRequest,
) -> Any:
    return _dispatch_impl(
        runner=runner,
        state=state,
        logger=logger,
        request=request,
    )


def _validate_decision_readiness(
    *,
    state: Any,
    decision: Any | None,
) -> ValidationResult | None:
    if decision is None or _decision_route_name(decision) != BRAIN_DECISION_ROUTE_ACT:
        return None
    for prefix, command in _iter_decision_commands(decision):
        readiness_issue = validate_command_readiness(command, prefix=prefix)
        if readiness_issue is not None:
            return ValidationResult(
                passed=False,
                code=readiness_issue.code,
                feedback=_build_readiness_feedback(
                    issue_code=readiness_issue.code,
                    field_path=readiness_issue.field_path,
                    placeholder_pattern=readiness_issue.placeholder_pattern,
                ),
                details={
                    "field_path": readiness_issue.field_path,
                    "placeholder_pattern": readiness_issue.placeholder_pattern,
                    "contextual_check": "",
                },
            )
        contextual_issue = _clarification_contextual_readiness_failure(
            state=state,
            command=command,
            prefix=prefix,
        )
        if contextual_issue is not None:
            field_path, clarify_summary = contextual_issue
            return ValidationResult(
                passed=False,
                code="decision_readiness_contextual_empty_payload",
                feedback=_build_readiness_feedback(
                    issue_code="decision_readiness_contextual_empty_payload",
                    field_path=field_path,
                    clarify_summary=clarify_summary,
                ),
                details={
                    "field_path": field_path,
                    "placeholder_pattern": "",
                    "contextual_check": "clarification_coherence",
                },
            )
    return None


def _iter_decision_commands(decision: Any) -> list[tuple[str, Any]]:
    commands: list[tuple[str, Any]] = []
    for index, command in enumerate(
        list(getattr(decision, "_seeded_commands", []) or [])
    ):
        commands.append((f"_seeded_commands[{index}]", command))
    return commands


def _clarification_contextual_readiness_failure(
    *,
    state: Any,
    command: Any,
    prefix: str,
) -> tuple[str, str] | None:
    if getattr(state, "pending_llm_clarify_context", None) is None:
        return None
    payload = command_payload_prefix(command, prefix=prefix)
    if payload is None:
        return None
    payload_prefix, payload_value = payload
    if not payload_is_contextually_empty(payload_value):
        return None
    return payload_prefix, _bounded_clarify_summary(state)


def _build_readiness_feedback(
    *,
    issue_code: str,
    field_path: str,
    placeholder_pattern: str = "",
    clarify_summary: str = "",
) -> str:
    if issue_code == "decision_readiness_contextual_empty_payload":
        message = (
            "Decision readiness validation failed: pending clarification context "
            f"exists, but `{field_path}` is empty or unresolved. Re-decide and "
            "either ask another clarification question or return an executable "
            "tool/agent payload with concrete values."
        )
        if clarify_summary:
            message += f" Pending clarification context: {clarify_summary}."
        return message
    pattern = str(placeholder_pattern or "").strip() or "<placeholder>"
    return (
        "Decision readiness validation failed: unresolved placeholder or "
        f"sentinel `{pattern}` found in `{field_path}`. Re-decide and return "
        "an executable tool/agent payload without placeholders, sentinels, or "
        "template markers."
    )


def _bounded_clarify_summary(state: Any) -> str:
    pending = getattr(state, "pending_llm_clarify_context", None)
    if pending is None:
        return ""
    parts: list[str] = []
    original_user_input = _truncate_text(
        str(getattr(pending, "original_user_input", "") or "").strip(),
        limit=120,
    )
    if original_user_input:
        parts.append(f"original_user_input={original_user_input!r}")
    known_context = dict(getattr(pending, "known_context", {}) or {})
    if known_context:
        items: list[str] = []
        for index, (key, value) in enumerate(known_context.items()):
            if index >= 4:
                items.append("...")
                break
            label = str(key or "").strip()
            text = _truncate_text(str(value or "").strip(), limit=40)
            if label and text:
                items.append(f"{label}={text!r}")
        if items:
            parts.append("known_context={" + ", ".join(items) + "}")
    clarify_question = _truncate_text(
        str(getattr(pending, "clarify_question", "") or "").strip(),
        limit=120,
    )
    if clarify_question:
        parts.append(f"clarify_question={clarify_question!r}")
    return "; ".join(parts)


def _truncate_text(text: str, *, limit: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    if limit <= 3:
        return normalized[:limit]
    return normalized[: limit - 3].rstrip() + "..."
