from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

from openminion.modules.llm.schemas import Message
from openminion.modules.brain.tools.parser import normalize_tool_name_for_brain

from .budget import _debit_llm_usage, _profile_budget_exhausted, _token_budget_exhausted
from .contracts import (
    ADAPTIVE_TERM_BUDGET_EXHAUSTED,
    ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
    ADAPTIVE_TERM_FINAL_TEXT,
    ADAPTIVE_TERM_LLM_ERROR,
    AdaptiveToolLoopContext,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopState,
    semantic_batch_signature,
)
from .status import emit_adaptive_status


def _direct_tool_turn_requested_batch_signature(
    loop_state: AdaptiveToolLoopState,
) -> str:
    direct_tool_turn = getattr(loop_state, "direct_tool_turn", None)
    return str(getattr(direct_tool_turn, "requested_batch_signature", "") or "").strip()


def _direct_tool_turn_requested_tool_names(
    loop_state: AdaptiveToolLoopState,
) -> tuple[str, ...]:
    direct_tool_turn = getattr(loop_state, "direct_tool_turn", None)
    return tuple(getattr(direct_tool_turn, "requested_tool_names", ()) or ())


def _direct_tool_turn_requested_calls(
    loop_state: AdaptiveToolLoopState,
) -> tuple[Any, ...]:
    direct_tool_turn = getattr(loop_state, "direct_tool_turn", None)
    return tuple(getattr(direct_tool_turn, "requested_calls", ()) or ())


def _direct_tool_turn_match_by_name_only(loop_state: AdaptiveToolLoopState) -> bool:
    direct_tool_turn = getattr(loop_state, "direct_tool_turn", None)
    return bool(getattr(direct_tool_turn, "match_by_name_only", False))


def _direct_tool_completed_tool_names(
    loop_state: AdaptiveToolLoopState,
) -> tuple[str, ...]:
    completed = loop_state.scratchpad.get("direct_tool_completed_tool_names", ())
    if not isinstance(completed, (list, tuple)):
        return ()
    normalized: list[str] = []
    for name in completed:
        canonical = _canonical_direct_tool_name(name)
        if canonical:
            normalized.append(canonical)
    return tuple(normalized)


def _canonical_direct_tool_name(raw_name: Any) -> str:
    token = str(raw_name or "").strip()
    if not token:
        return ""
    normalized = normalize_tool_name_for_brain(token)
    if normalized:
        return normalized
    if "." in token:
        family = str(token.split(".", 1)[0] or "").strip()
        family_normalized = normalize_tool_name_for_brain(family)
        if family_normalized:
            return family_normalized
    return token


def _direct_tool_call_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments)
    return {}


def _direct_tool_call_inputs(raw_inputs: Any) -> dict[str, Any]:
    if isinstance(raw_inputs, dict):
        return dict(raw_inputs)
    return {}


def _direct_tool_call_with_requested_contract(
    *,
    tool_call: Any,
    requested_name: str,
    requested_arguments: dict[str, Any],
    requested_inputs: dict[str, Any],
) -> Any:
    if requested_inputs:
        return SimpleNamespace(
            id=getattr(tool_call, "id", None),
            name=requested_name,
            arguments=requested_arguments,
            inputs=requested_inputs,
        )
    if hasattr(tool_call, "model_copy"):
        return tool_call.model_copy(
            update={"arguments": requested_arguments},
            deep=True,
        )
    return SimpleNamespace(
        name=requested_name,
        arguments=requested_arguments,
    )


def _direct_tool_requested_call_matches(
    *,
    requested_name: str,
    requested_arguments: dict[str, Any],
    executed_name: str,
    executed_arguments: dict[str, Any],
) -> bool:
    if executed_name != requested_name:
        return False
    for key, value in requested_arguments.items():
        if executed_arguments.get(key) != value:
            return False
    return True


def _allow_homogeneous_batch_for_single_requested_call(
    *,
    requested_call: Any,
    tool_calls: list[Any],
) -> bool:
    if len(tool_calls) <= 1:
        return False
    requested_name = _canonical_direct_tool_name(
        str(getattr(requested_call, "name", "") or "").strip()
    )
    requested_arguments = _direct_tool_call_arguments(
        getattr(requested_call, "arguments", None)
    )
    if not requested_name:
        return False
    matched_requested_call = False
    for tool_call in tool_calls:
        executed_name = _canonical_direct_tool_name(
            str(getattr(tool_call, "name", "") or "").strip()
        )
        executed_arguments = _direct_tool_call_arguments(
            getattr(tool_call, "arguments", None)
        )
        if executed_name != requested_name:
            return False
        if _direct_tool_requested_call_matches(
            requested_name=requested_name,
            requested_arguments=requested_arguments,
            executed_name=executed_name,
            executed_arguments=executed_arguments,
        ):
            matched_requested_call = True
    return matched_requested_call


def _remaining_direct_tool_name_sequence(
    loop_state: AdaptiveToolLoopState,
) -> tuple[str, ...]:
    requested = _direct_tool_turn_requested_tool_names(loop_state)
    if not requested:
        return ()
    completed = _direct_tool_completed_tool_names(loop_state)
    if not completed:
        return requested
    if requested[: len(completed)] != completed:
        return requested
    return requested[len(completed) :]


def _store_direct_tool_completed_tool_names(
    loop_state: AdaptiveToolLoopState,
    completed: tuple[str, ...],
) -> None:
    loop_state.scratchpad["direct_tool_completed_tool_names"] = list(completed)


def _direct_tool_turn_active(loop_state: AdaptiveToolLoopState) -> bool:
    return bool(
        _direct_tool_turn_requested_batch_signature(loop_state)
        or (
            _direct_tool_turn_match_by_name_only(loop_state)
            and _direct_tool_turn_requested_tool_names(loop_state)
        )
    )


def _visible_tool_specs_for_direct_tool_turn(
    loop_state: AdaptiveToolLoopState,
    tool_specs: list[Any],
) -> list[Any]:
    if not _direct_tool_turn_active(loop_state) or bool(
        getattr(loop_state, "direct_tool_requested_batch_satisfied", False)
    ):
        return tool_specs
    requested_tool_names = _remaining_direct_tool_name_sequence(loop_state)
    if not requested_tool_names:
        return tool_specs
    specs_by_name = {
        str(getattr(tool_spec, "name", "") or "").strip(): tool_spec
        for tool_spec in tool_specs
    }
    restricted: list[Any] = []
    seen_names: set[str] = set()
    for name in requested_tool_names:
        if name in seen_names or name not in specs_by_name:
            continue
        restricted.append(specs_by_name[name])
        seen_names.add(name)
    if restricted:
        loop_state.scratchpad["direct_tool_visible_tools_restricted"] = True
        return restricted
    return tool_specs


def _restore_direct_tool_specs_after_shortlist(
    *,
    loop_state: AdaptiveToolLoopState,
    active_tool_specs: list[Any],
    requestable_tool_specs: list[Any] | tuple[Any, ...] | None,
) -> list[Any]:
    if not _direct_tool_turn_active(loop_state) or not requestable_tool_specs:
        return active_tool_specs
    requested_tool_names = _remaining_direct_tool_name_sequence(loop_state)
    if not requested_tool_names:
        return active_tool_specs

    active_by_name = {
        str(getattr(tool_spec, "name", "") or "").strip(): tool_spec
        for tool_spec in active_tool_specs
    }
    requestable_by_name = {
        str(getattr(tool_spec, "name", "") or "").strip(): tool_spec
        for tool_spec in requestable_tool_specs
    }

    restored: list[Any] = []
    for name in requested_tool_names:
        if name in active_by_name or name not in requestable_by_name:
            continue
        restored.append(requestable_by_name[name])

    if not restored:
        return active_tool_specs

    restored_names = [
        str(getattr(tool_spec, "name", "") or "").strip()
        for tool_spec in restored
        if str(getattr(tool_spec, "name", "") or "").strip()
    ]
    loop_state.scratchpad["direct_tool_shortlist_restored_tools"] = restored_names
    return [*active_tool_specs, *restored]


def _build_direct_tool_closure_message(
    loop_state: AdaptiveToolLoopState,
) -> Message:
    direct_tool_turn = getattr(loop_state, "direct_tool_turn", None)
    requested_tools = list(getattr(direct_tool_turn, "requested_tool_names", ()) or ())
    rendered_tools = (
        ", ".join(requested_tools) if requested_tools else "the requested tool batch"
    )
    # rail content is fact + hard structural
    return Message(
        role="system",
        content=(
            f"The explicit requested tool batch ({rendered_tools}) already completed "
            "successfully for this turn. Do not call more tools."
        ),
    )


def _direct_tool_batch_completed_successfully(
    *,
    loop_state: AdaptiveToolLoopState,
    signature: str,
    ordered_tool_results: list[tuple[Any, Any]],
    profile: AdaptiveToolLoopProfile,
) -> bool:
    if not _direct_tool_turn_active(loop_state):
        return False
    if not ordered_tool_results:
        return False
    if _direct_tool_turn_match_by_name_only(loop_state):
        executed_tool_names = tuple(
            _canonical_direct_tool_name(
                str(
                    getattr(
                        getattr(command_outcome, "approved_command", None),
                        "tool_name",
                        "",
                    )
                    or ""
                ).strip()
                or str(getattr(tool_call, "name", "") or "").strip()
            )
            for tool_call, command_outcome in ordered_tool_results
        )
        remaining_tool_names = _remaining_direct_tool_name_sequence(loop_state)
        if not remaining_tool_names:
            return False
        expected_prefix = remaining_tool_names[: len(executed_tool_names)]
        if executed_tool_names != expected_prefix:
            return False
    elif len(_direct_tool_turn_requested_calls(loop_state)) == 1:
        requested_call = _direct_tool_turn_requested_calls(loop_state)[0]
        requested_name = _canonical_direct_tool_name(
            str(getattr(requested_call, "name", "") or "").strip()
        )
        if not ordered_tool_results or not requested_name:
            return False
        requested_arguments = _direct_tool_call_arguments(
            getattr(requested_call, "arguments", None)
        )
        matched_requested_call = False
        for tool_call, command_outcome in ordered_tool_results:
            approved_command = getattr(command_outcome, "approved_command", None)
            executed_name = _canonical_direct_tool_name(
                str(getattr(approved_command, "tool_name", "") or "").strip()
            )
            if not executed_name:
                executed_name = _canonical_direct_tool_name(
                    str(getattr(tool_call, "name", "") or "").strip()
                )
            executed_arguments = getattr(approved_command, "args", None)
            if not isinstance(executed_arguments, dict):
                executed_arguments = _direct_tool_call_arguments(
                    getattr(tool_call, "arguments", None)
                )
            if executed_name != requested_name:
                return False
            if _direct_tool_requested_call_matches(
                requested_name=requested_name,
                requested_arguments=requested_arguments,
                executed_name=executed_name,
                executed_arguments=executed_arguments,
            ):
                matched_requested_call = True
        if not matched_requested_call:
            return False
    elif _completed_direct_tool_batch_signature(
        signature=signature,
        ordered_tool_results=ordered_tool_results,
    ) != _direct_tool_turn_requested_batch_signature(loop_state):
        return False
    if not all(getattr(item[1], "job", None) is None for item in ordered_tool_results):
        return False
    for _, command_outcome in ordered_tool_results:
        action_result = getattr(command_outcome, "action_result", None)
        status = str(getattr(action_result, "status", "") or "").strip().lower()
        if status != "success":
            return False
    if not profile.stop_on_job_pending and any(
        getattr(item[1], "job", None) is not None for item in ordered_tool_results
    ):
        return False
    if _direct_tool_turn_match_by_name_only(loop_state):
        completed_tool_names = (
            _direct_tool_completed_tool_names(loop_state) + executed_tool_names
        )
        _store_direct_tool_completed_tool_names(loop_state, completed_tool_names)
        return completed_tool_names == _direct_tool_turn_requested_tool_names(
            loop_state
        )
    return True


def _completed_direct_tool_batch_signature(
    *,
    signature: str,
    ordered_tool_results: list[tuple[Any, Any]],
) -> str:
    executed_batch: list[Any] = []
    for tool_call, command_outcome in ordered_tool_results:
        approved_command = getattr(command_outcome, "approved_command", None)
        tool_name = str(getattr(approved_command, "tool_name", "") or "").strip()
        args = getattr(approved_command, "args", None)
        if not tool_name:
            tool_name = str(getattr(tool_call, "name", "") or "").strip()
        if not isinstance(args, dict):
            fallback_args = getattr(tool_call, "arguments", None)
            args = (
                dict(fallback_args or {}) if isinstance(fallback_args, dict) else None
            )
        if not tool_name or not isinstance(args, dict):
            return signature
        executed_batch.append(SimpleNamespace(name=tool_name, arguments=dict(args)))
    if not executed_batch:
        return signature
    return semantic_batch_signature(executed_batch)


def _should_force_direct_tool_closure(
    loop_state: AdaptiveToolLoopState,
) -> bool:
    return (
        _direct_tool_turn_active(loop_state)
        and bool(getattr(loop_state, "direct_tool_requested_batch_satisfied", False))
        and not bool(getattr(loop_state, "direct_tool_closure_consumed", False))
    )


def _clamp_direct_tool_batch_to_requested_call(
    loop_state: AdaptiveToolLoopState,
    tool_calls: list[Any],
) -> list[Any]:
    requested_batch_signature = _direct_tool_turn_requested_batch_signature(loop_state)
    requested_calls = _direct_tool_turn_requested_calls(loop_state)
    if (
        not requested_batch_signature
        and not _direct_tool_turn_match_by_name_only(loop_state)
        or bool(getattr(loop_state, "direct_tool_requested_batch_satisfied", False))
    ):
        return tool_calls
    if _direct_tool_turn_match_by_name_only(loop_state):
        remaining_tool_names = _remaining_direct_tool_name_sequence(loop_state)
        if not remaining_tool_names:
            return tool_calls
        requested_name = (
            remaining_tool_names[0] if len(remaining_tool_names) == 1 else ""
        )
        if requested_name:
            for tool_call in tool_calls:
                if str(getattr(tool_call, "name", "") or "").strip() != requested_name:
                    continue
                loop_state.scratchpad["direct_tool_requested_batch_clamped"] = True
                return [tool_call]
        if remaining_tool_names:
            matching: list[Any] = []
            remaining_index = 0
            for tool_call in tool_calls:
                if remaining_index >= len(remaining_tool_names):
                    break
                tool_name = str(getattr(tool_call, "name", "") or "").strip()
                if tool_name != remaining_tool_names[remaining_index]:
                    if matching:
                        break
                    continue
                matching.append(tool_call)
                remaining_index += 1
            if matching:
                loop_state.scratchpad["direct_tool_requested_batch_clamped"] = True
                return matching
        loop_state.scratchpad["direct_tool_requested_batch_clamped_empty"] = True
        return []
    if len(requested_calls) == 1:
        requested_call = requested_calls[0]
        requested_name = str(getattr(requested_call, "name", "") or "").strip()
        requested_inputs = _direct_tool_call_inputs(
            getattr(requested_call, "inputs", None)
        )
        if _allow_homogeneous_batch_for_single_requested_call(
            requested_call=requested_call,
            tool_calls=tool_calls,
        ):
            loop_state.scratchpad["direct_tool_requested_batch_clamped"] = False
            loop_state.scratchpad["direct_tool_requested_batch_expanded"] = True
            return tool_calls
        for tool_call in tool_calls:
            if str(getattr(tool_call, "name", "") or "").strip() != requested_name:
                continue
            loop_state.scratchpad["direct_tool_requested_batch_clamped"] = True
            requested_arguments = _direct_tool_call_arguments(
                getattr(requested_call, "arguments", None)
            )
            return [
                _direct_tool_call_with_requested_contract(
                    tool_call=tool_call,
                    requested_name=requested_name,
                    requested_arguments=requested_arguments,
                    requested_inputs=requested_inputs,
                )
            ]
    if len(requested_calls) > 1:
        clamped_calls: list[Any] = []
        for tool_call in tool_calls:
            executed_name = str(getattr(tool_call, "name", "") or "").strip()
            executed_arguments = _direct_tool_call_arguments(
                getattr(tool_call, "arguments", None)
            )
            for requested_call in requested_calls:
                requested_name = str(getattr(requested_call, "name", "") or "").strip()
                requested_arguments = _direct_tool_call_arguments(
                    getattr(requested_call, "arguments", None)
                )
                if not _direct_tool_requested_call_matches(
                    requested_name=requested_name,
                    requested_arguments=requested_arguments,
                    executed_name=executed_name,
                    executed_arguments=executed_arguments,
                ):
                    continue
                clamped_calls.append(
                    _direct_tool_call_with_requested_contract(
                        tool_call=tool_call,
                        requested_name=requested_name,
                        requested_arguments=requested_arguments,
                        requested_inputs=_direct_tool_call_inputs(
                            getattr(requested_call, "inputs", None)
                        ),
                    )
                )
                break
        if clamped_calls:
            loop_state.scratchpad["direct_tool_requested_batch_clamped"] = True
            return clamped_calls
    for tool_call in tool_calls:
        if semantic_batch_signature([tool_call]) != requested_batch_signature:
            continue
        loop_state.scratchpad["direct_tool_requested_batch_clamped"] = True
        return [tool_call]
    return tool_calls


def _force_direct_tool_answer_only_closure(
    *,
    loop_ctx: AdaptiveToolLoopContext,
    profile: AdaptiveToolLoopProfile,
    loop_state: AdaptiveToolLoopState,
    runtime: Any,
    model: str,
    tool_specs: list[Any],
    max_output_tokens: int | None,
    metadata: dict[str, Any] | None,
    allowed_tools: frozenset[str],
    public_mode_tag: str,
) -> tuple[AdaptiveToolLoopOutcome | None, int, int]:
    if not _should_force_direct_tool_closure(loop_state):
        return None, 0, 0
    if _token_budget_exhausted(loop_ctx, loop_state) or _profile_budget_exhausted(
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
        return (
            AdaptiveToolLoopOutcome(
                profile_name=profile.profile_name,
                mode_name=profile.mode_name,
                termination_reason=ADAPTIVE_TERM_BUDGET_EXHAUSTED,
                state=loop_state,
                allowed_tools=allowed_tools,
            ),
            0,
            0,
        )
    loop_state.direct_tool_closure_consumed = True
    loop_state.scratchpad["direct_tool_closure_forced"] = True
    loop_state.messages.append(_build_direct_tool_closure_message(loop_state))
    emit_adaptive_status(
        loop_ctx,
        profile=profile,
        loop_state=loop_state,
        detail_text=f"{public_mode_tag} answer-only closure",
        mode_state="direct_tool_closure",
    )
    closure_start = time.monotonic()
    try:
        response = runtime.complete(
            messages=loop_state.messages,
            tools=[],
            model=model,
            tool_choice="none",
            max_output_tokens=int(max_output_tokens)
            if max_output_tokens is not None
            else None,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        loop_state.termination_reason = ADAPTIVE_TERM_LLM_ERROR
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} answer-only closure failed",
            mode_state="llm_error",
            termination_reason=ADAPTIVE_TERM_LLM_ERROR,
        )
        return (
            AdaptiveToolLoopOutcome(
                profile_name=profile.profile_name,
                mode_name=profile.mode_name,
                termination_reason=ADAPTIVE_TERM_LLM_ERROR,
                state=loop_state,
                allowed_tools=allowed_tools,
                error_message=str(exc),
            ),
            0,
            0,
        )
    duration_ms = int((time.monotonic() - closure_start) * 1000)
    usage = getattr(response, "usage", None)
    tokens_used = int(getattr(usage, "input_tokens", 0) or 0) + int(
        getattr(usage, "output_tokens", 0) or 0
    )
    _debit_llm_usage(loop_ctx, response)
    loop_state.llm_calls += 1
    for assistant_message in list(getattr(response, "assistant_messages", []) or []):
        loop_state.messages.append(assistant_message)
    if not bool(getattr(response, "ok", False)):
        error = getattr(response, "error", None)
        error_message = str(getattr(error, "message", "") or "LLM returned not-ok")
        loop_state.termination_reason = ADAPTIVE_TERM_LLM_ERROR
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} answer-only closure error",
            mode_state="llm_error",
            termination_reason=ADAPTIVE_TERM_LLM_ERROR,
        )
        return (
            AdaptiveToolLoopOutcome(
                profile_name=profile.profile_name,
                mode_name=profile.mode_name,
                termination_reason=ADAPTIVE_TERM_LLM_ERROR,
                state=loop_state,
                allowed_tools=allowed_tools,
                error_message=error_message,
            ),
            duration_ms,
            tokens_used,
        )
    if list(getattr(response, "tool_calls", []) or []):
        loop_state.termination_reason = ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} answer-only closure failed",
            mode_state="direct_tool_closure_failed",
            termination_reason=ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
        )
        return (
            AdaptiveToolLoopOutcome(
                profile_name=profile.profile_name,
                mode_name=profile.mode_name,
                termination_reason=ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
                state=loop_state,
                allowed_tools=allowed_tools,
                error_message=(
                    "Answer-only closure returned more tool calls after the "
                    "requested direct-tool batch had already completed."
                ),
            ),
            duration_ms,
            tokens_used,
        )
    final_text = str(getattr(response, "output_text", "") or "").strip()
    if not final_text:
        loop_state.termination_reason = ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED
        emit_adaptive_status(
            loop_ctx,
            profile=profile,
            loop_state=loop_state,
            detail_text=f"{public_mode_tag} answer-only closure failed",
            mode_state="direct_tool_closure_failed",
            termination_reason=ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
        )
        return (
            AdaptiveToolLoopOutcome(
                profile_name=profile.profile_name,
                mode_name=profile.mode_name,
                termination_reason=ADAPTIVE_TERM_DIRECT_TOOL_CLOSURE_FAILED,
                state=loop_state,
                allowed_tools=allowed_tools,
                error_message=(
                    "Answer-only closure did not return a final answer after the "
                    "requested direct-tool batch had already completed."
                ),
            ),
            duration_ms,
            tokens_used,
        )
    loop_state.termination_reason = ADAPTIVE_TERM_FINAL_TEXT
    return (
        AdaptiveToolLoopOutcome(
            profile_name=profile.profile_name,
            mode_name=profile.mode_name,
            termination_reason=ADAPTIVE_TERM_FINAL_TEXT,
            state=loop_state,
            allowed_tools=allowed_tools,
            final_text=final_text,
        ),
        duration_ms,
        tokens_used,
    )
