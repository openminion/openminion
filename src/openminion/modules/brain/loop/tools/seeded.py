from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_FAILURES,
    BRAIN_ACTION_STATUS_BLOCKED,
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_SUCCESS,
)
from openminion.modules.llm.schemas import Message

from .contracts import (
    ADAPTIVE_CLOSURE_ENGINE_SINGLE_PASS,
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_DISALLOWED_TOOL,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_JOB_PENDING,
    ADAPTIVE_TERM_NEEDS_USER,
    ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
    AdaptiveToolLoopOutcome,
    profile_include_reflect,
)
from .status import emit_adaptive_status

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .contracts import (
        AdaptiveToolLoopContext,
        AdaptiveToolLoopProfile,
        AdaptiveToolLoopState,
    )


def _policy_denial_details(action_result: Any) -> dict[str, Any] | None:
    error_obj = getattr(action_result, "error", None)
    error_code = str(getattr(error_obj, "code", "") or "").strip().upper()
    details = getattr(error_obj, "details", None)
    outputs = getattr(action_result, "outputs", None)
    nested_error = outputs.get("error") if isinstance(outputs, dict) else None
    if error_code != "POLICY_DENIED" and isinstance(nested_error, dict):
        error_code = str(nested_error.get("code", "") or "").strip().upper()
    if not isinstance(details, dict) and isinstance(nested_error, dict):
        nested_details = nested_error.get("details")
        if isinstance(nested_details, dict):
            details = nested_details
    if error_code != "POLICY_DENIED":
        return None
    if not isinstance(details, dict):
        return None
    return details


def _policy_denial_recovery_message(action_result: Any) -> str | None:
    details = _policy_denial_details(action_result)
    if details is None:
        return None
    suggested_tool = str(details.get("suggested_tool", "") or "").strip()
    if not suggested_tool:
        return None
    suggested_fix = str(details.get("suggested_fix", "") or "").strip()
    blocked_tool = str(details.get("tool_name", "") or "").strip() or "tool"
    message = (
        f"The seeded {blocked_tool} command was blocked by policy. Do not "
        f"repeat it. Retry the same user task using {suggested_tool} if that "
        "structured tool can satisfy the intent."
    )
    return f"{message} {suggested_fix}" if suggested_fix else message


def _invalid_workdir_recovery_message(action_result: Any) -> str | None:
    error_obj = getattr(action_result, "error", None)
    error_code = str(getattr(error_obj, "code", "") or "").strip().upper()
    details = getattr(error_obj, "details", None)
    message = str(getattr(error_obj, "message", "") or "").strip()
    outputs = getattr(action_result, "outputs", None)
    nested_error = outputs.get("error") if isinstance(outputs, dict) else None
    if error_code != "INVALID_ARGUMENT" and isinstance(nested_error, dict):
        error_code = str(nested_error.get("code", "") or "").strip().upper()
    if not message and isinstance(nested_error, dict):
        message = str(nested_error.get("message", "") or "").strip()
    if not isinstance(details, dict) and isinstance(nested_error, dict):
        nested_details = nested_error.get("details")
        if isinstance(nested_details, dict):
            details = nested_details
    if error_code != "INVALID_ARGUMENT" or not isinstance(details, dict):
        return None
    workdir = str(details.get("workdir", "") or "").strip()
    if not workdir or "workdir" not in message.lower():
        return None
    return (
        "The seeded exec.run command used a workdir that does not exist. "
        "Do not repeat it. Retry the same user task using the absolute workspace "
        "directory from the original request as exec.run workdir, or use file tools "
        "with absolute paths for inspection before running verification."
    )


def _confirmed_tool_failure_recovery_message(action_result: Any) -> str | None:
    status = str(getattr(action_result, "status", "") or "").strip()
    if status == BRAIN_ACTION_STATUS_SUCCESS:
        return None
    error_obj = getattr(action_result, "error", None)
    error_code = str(getattr(error_obj, "code", "") or "").strip()
    error_message = str(getattr(error_obj, "message", "") or "").strip()
    summary = str(getattr(action_result, "summary", "") or "").strip()
    outputs = getattr(action_result, "outputs", None)
    if isinstance(outputs, dict):
        if not error_code:
            nested_error = outputs.get("error")
            if isinstance(nested_error, dict):
                error_code = str(nested_error.get("code", "") or "").strip()
                error_message = str(nested_error.get("message", "") or "").strip()
        stderr_preview = str(
            outputs.get("stderr_preview") or outputs.get("stderr") or ""
        ).strip()
        stdout_preview = str(
            outputs.get("stdout_preview") or outputs.get("stdout") or ""
        ).strip()
    else:
        stderr_preview = ""
        stdout_preview = ""
    details = [
        item
        for item in (
            f"code={error_code}" if error_code else "",
            f"summary={summary}" if summary else "",
            f"message={error_message}" if error_message else "",
            f"stderr={stderr_preview}" if stderr_preview else "",
            f"stdout={stdout_preview}" if stdout_preview else "",
        )
        if item
    ]
    suffix = f" Failure details: {'; '.join(details)}." if details else ""
    return (
        "The confirmed seeded tool command failed. Do not treat that failed "
        "command as the final answer and do not repeat it blindly. Continue the "
        "same user task by correcting the command arguments, using the requested "
        "verification command, or switching to the appropriate structured tool."
        f"{suffix}"
    )


def _seeded_failure_recovery_message(action_result: Any) -> str | None:
    return (
        _policy_denial_recovery_message(action_result)
        or _invalid_workdir_recovery_message(action_result)
        or _confirmed_tool_failure_recovery_message(action_result)
    )


def _run_seeded_command_step(
    *,
    loop_ctx: "AdaptiveToolLoopContext",
    profile: "AdaptiveToolLoopProfile",
    loop_state: "AdaptiveToolLoopState",
    seeded_queue: list[Any],
    allowed_tools: set[str],
    public_mode_tag: str,
    finalizer: Callable[[AdaptiveToolLoopOutcome], Any] | None,
    on_tool_result: Callable[["AdaptiveToolLoopState"], None] | None,
    build_missing_action_result: Callable[[str], Any],
    append_tool_result_payload: Callable[..., None],
    token_budget_exhausted: Callable[
        ["AdaptiveToolLoopContext", "AdaptiveToolLoopState"], bool
    ],
    profile_budget_exhausted: Callable[..., bool],
) -> tuple[bool, AdaptiveToolLoopOutcome | None]:
    if not seeded_queue:
        return False, None
    if token_budget_exhausted(loop_ctx, loop_state) or profile_budget_exhausted(
        profile=profile,
        state=loop_state,
    ):
        loop_state.termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} budget exhausted",
            mode_state="budget_exhausted",
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        )
        return True, AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=loop_state,
            allowed_tools=allowed_tools,
        )

    loop_state.iteration += 1
    command = seeded_queue.pop(0)
    command_kind = str(getattr(command, "kind", "") or "").strip().lower()
    tool_name = str(getattr(command, "tool_name", "") or "").strip()
    command_label = (
        str(getattr(command, "title", "") or "").strip()
        or tool_name
        or command_kind
        or "command"
    )
    if command_kind == "tool":
        if tool_name not in allowed_tools:
            message = (
                f"{profile.mode_name} does not allow tool {tool_name!r}. "
                f"Allowed: {sorted(allowed_tools)}"
            )
            loop_state.termination_reason = ADAPTIVE_TERM_DISALLOWED_TOOL
            emit_adaptive_status(
                loop_ctx,
                profile=profile,
                loop_state=loop_state,
                detail_text=f"{public_mode_tag} disallowed tool: {tool_name}",
                mode_state="disallowed_tool",
                termination_reason=ADAPTIVE_TERM_DISALLOWED_TOOL,
                extra={"tool_name": tool_name},
            )
            return True, AdaptiveToolLoopOutcome(
                profile_name=profile.profile_name,
                mode_name=profile.mode_name,
                termination_reason=ADAPTIVE_TERM_DISALLOWED_TOOL,
                state=loop_state,
                allowed_tools=allowed_tools,
                error_message=message,
                tool_name=tool_name,
            )
        loop_state.tool_calls_made.append(tool_name)
        loop_state.total_tool_calls += 1

    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} command {command_label}",
        mode_state="tool_call",
        extra={"tool_name": tool_name or command_kind or command_label},
    )
    command_outcome = loop_ctx.execute_command(
        command=command,
        include_reflect=profile_include_reflect(profile),
    )
    action_result = command_outcome.action_result or build_missing_action_result(
        tool_name or command_label
    )
    if command_kind == "tool":
        append_tool_result_payload(
            loop_state,
            tool_name=tool_name or command_label,
            action_result=action_result,
        )
    results = list(loop_state.scratchpad.get("seeded_command_results", []) or [])
    results.append(
        {
            "kind": command_kind,
            "label": command_label,
            "status": str(getattr(action_result, "status", "") or ""),
            "summary": str(getattr(action_result, "summary", "") or ""),
        }
    )
    loop_state.scratchpad["seeded_command_results"] = results

    if command_outcome.job is not None and profile.stop_on_job_pending:
        loop_state.termination_reason = ADAPTIVE_TERM_JOB_PENDING
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} job pending",
            mode_state="job_pending",
            termination_reason=ADAPTIVE_TERM_JOB_PENDING,
        )
        return True, AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_JOB_PENDING,
            state=loop_state,
            allowed_tools=allowed_tools,
            action_result=action_result,
            job=command_outcome.job,
        )
    if (
        action_result.status == BRAIN_ACTION_STATUS_NEEDS_USER
        and profile.stop_on_needs_user
    ):
        loop_state.termination_reason = ADAPTIVE_TERM_NEEDS_USER
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} waiting for approval",
            mode_state="needs_user",
            termination_reason=ADAPTIVE_TERM_NEEDS_USER,
        )
        return True, AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_NEEDS_USER,
            state=loop_state,
            allowed_tools=allowed_tools,
            action_result=action_result,
        )
    if (
        action_result.status == BRAIN_ACTION_STATUS_BLOCKED
        and getattr(getattr(action_result, "error", None), "code", "")
        == "BUDGET_EXCEEDED"
    ):
        loop_state.termination_reason = ADAPTIVE_TERM_BUDGET_EXHAUSTED
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} budget exhausted",
            mode_state="budget_exhausted",
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
        )
        return True, AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
            state=loop_state,
            allowed_tools=allowed_tools,
            action_result=action_result,
        )
    recovery_message = None
    if (
        action_result.status != BRAIN_ACTION_STATUS_SUCCESS
        and profile.allow_llm_recovery_after_tool_failure
    ):
        recovery_message = _seeded_failure_recovery_message(action_result)
    if recovery_message:
        seeded_queue.clear()
        loop_state.messages.append(Message(role="system", content=recovery_message))
        return True, None

    if action_result.status in BRAIN_ACTION_STATUS_FAILURES:
        loop_state.termination_reason = ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} command failure",
            mode_state="tool_failure",
            termination_reason=ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
            extra={"tool_name": tool_name or command_kind or command_label},
        )
        return True, AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_TOOL_FAILURE_NO_RECOVERY,
            state=loop_state,
            allowed_tools=allowed_tools,
            action_result=action_result,
            tool_name=tool_name or command_kind or command_label,
        )

    loop_ctx.advance_after_action(action_result=action_result)
    if on_tool_result is not None:
        on_tool_result(loop_state)
    if seeded_queue:
        return True, None

    loop_state.termination_reason = ADAPTIVE_TERM_FINAL_TEXT
    outcome = AdaptiveToolLoopOutcome(
        profile_name=profile.profile_name,
        mode_name=profile.mode_name,
        termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
        state=loop_state,
        allowed_tools=allowed_tools,
        final_text="",
        action_result=action_result,
    )
    if (
        profile.final_closure_policy == ADAPTIVE_CLOSURE_ENGINE_SINGLE_PASS
        and finalizer is not None
    ):
        outcome.mode_result = finalizer(outcome)
    return True, outcome
