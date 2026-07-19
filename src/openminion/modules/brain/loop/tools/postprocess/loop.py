from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from openminion.modules.brain.constants import STATE_KEY_MODULE_STATE
from openminion.modules.brain.loop.constants import MUTATING_FILE_REPEAT_CLOSEOUT_LIMIT
from openminion.modules.llm.schemas import Message

from ..duplicate_batch import _reset_duplicate_batch_tracking
from ..iteration.helpers import _MUTATING_FILE_TOOLS
from ..contracts import AdaptiveToolLoopContext, AdaptiveToolLoopProfile
from .mutation_closeout import (
    MUTATING_FILE_CLOSEOUT_KEY,
    MUTATING_FILE_PATH_COUNTS_KEY,
)
from ..snapshot import LoopSnapshot, LoopToolCallRecord, compress_transcript
from ..telemetry import _emit_iteration_event


def _has_successful_mutating_file_tool_result(
    ordered_tool_results: list[tuple[Any, Any]],
) -> bool:
    for tool_call, command_outcome in ordered_tool_results:
        tool_name = str(getattr(tool_call, "name", "") or "").strip()
        if tool_name not in _MUTATING_FILE_TOOLS:
            continue
        action_result = getattr(command_outcome, "action_result", None)
        status = str(getattr(action_result, "status", "") or "").strip().lower()
        if status == "success":
            return True
    return False


def _reset_successful_mutation_repetition_tracking(
    loop_state: Any,
    *,
    iteration_tool_sequences: list[str],
) -> None:
    _reset_duplicate_batch_tracking(loop_state)
    loop_state.seen_signatures = []
    iteration_tool_sequences.clear()


def _normalized_mutating_result_path(raw_path: Any) -> str | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if path.is_absolute():
        return path.resolve(strict=False).as_posix()
    return PurePosixPath(text.replace("\\", "/")).as_posix()


def _mutating_file_result_path(tool_call: Any, command_outcome: Any) -> str | None:
    action_result = getattr(command_outcome, "action_result", None)
    outputs = getattr(action_result, "outputs", None)
    if isinstance(outputs, dict):
        path = _normalized_mutating_result_path(outputs.get("path"))
        if path is not None:
            return path
    arguments = dict(getattr(tool_call, "arguments", {}) or {})
    return _normalized_mutating_result_path(arguments.get("path"))


def _record_mutating_file_repetition(
    loop_state: Any,
    ordered_tool_results: list[tuple[Any, Any]],
) -> bool:
    scratchpad = dict(loop_state.scratchpad or {})
    counts = scratchpad.get(MUTATING_FILE_PATH_COUNTS_KEY)
    if not isinstance(counts, dict):
        counts = {}
    closeout_paths: list[str] = []
    for tool_call, command_outcome in ordered_tool_results:
        tool_name = str(getattr(tool_call, "name", "") or "").strip()
        if tool_name not in _MUTATING_FILE_TOOLS:
            continue
        action_result = getattr(command_outcome, "action_result", None)
        status = str(getattr(action_result, "status", "") or "").strip().lower()
        if status != "success":
            continue
        path = _mutating_file_result_path(tool_call, command_outcome)
        if path is None:
            continue
        count = int(counts.get(path, 0) or 0) + 1
        counts[path] = count
        if count >= MUTATING_FILE_REPEAT_CLOSEOUT_LIMIT:
            closeout_paths.append(path)
    scratchpad[MUTATING_FILE_PATH_COUNTS_KEY] = counts
    if closeout_paths:
        scratchpad[MUTATING_FILE_CLOSEOUT_KEY] = True
        scratchpad["mutating_file_repeated_paths"] = closeout_paths[-5:]
    loop_state.scratchpad = scratchpad
    return bool(closeout_paths)


def _mutating_file_closeout_message(loop_state: Any) -> Message:
    paths = [
        str(path or "").strip()
        for path in list(
            dict(loop_state.scratchpad or {}).get("mutating_file_repeated_paths", [])
            or []
        )
        if str(path or "").strip()
    ]
    rendered_paths = ", ".join(paths[-3:]) if paths else "the same file path"
    return Message(
        role="system",
        content=(
            "The same mutating file path has already been successfully updated "
            f"multiple times in this loop ({rendered_paths}). Stop calling file "
            "mutation tools. Use the existing successful tool evidence and return "
            "the final user-facing answer now, preserving requested result labels, "
            "files-changed summaries, validation status, and follow-up labels."
        ),
    )


def _track_successful_mutating_file_progress(
    loop_state: Any,
    ordered_tool_results: list[tuple[Any, Any]],
    *,
    iteration_tool_sequences: list[str],
) -> None:
    if not _has_successful_mutating_file_tool_result(ordered_tool_results):
        return
    repeated_mutation = _record_mutating_file_repetition(
        loop_state,
        ordered_tool_results,
    )
    _reset_successful_mutation_repetition_tracking(
        loop_state,
        iteration_tool_sequences=iteration_tool_sequences,
    )
    if repeated_mutation:
        loop_state.messages.append(_mutating_file_closeout_message(loop_state))


def finalize_iteration_state(
    loop_ctx: AdaptiveToolLoopContext,
    *,
    profile: AdaptiveToolLoopProfile,
    loop_state: Any,
    batch_had_progress: bool,
    signature: str,
    ordered_tool_results: list[tuple[Any, Any]],
    tool_calls: list[Any],
    prefetch_predictor: Any,
    prefetch_pending: Any,
    loop_cache: Any,
    loop_profiler: Any,
    iter_llm_duration_ms: int,
    iter_tool_records: list[Any],
    iter_input_tokens: int,
    iter_output_tokens: int,
    turn_scope_id: Any,
    model: str,
    public_mode_name: str,
    record_duplicate_batch_execution_facts: Any,
    direct_tool_batch_completed_successfully: Any,
    iteration_tool_sequences: list[str],
) -> Any:
    _track_successful_mutating_file_progress(
        loop_state,
        ordered_tool_results,
        iteration_tool_sequences=iteration_tool_sequences,
    )
    if batch_had_progress and signature not in set(loop_state.seen_signatures):
        loop_state.seen_signatures.append(signature)
    if batch_had_progress:
        record_duplicate_batch_execution_facts(
            loop_state,
            signature=signature,
            ordered_tool_results=ordered_tool_results,
        )
    if direct_tool_batch_completed_successfully(
        loop_state=loop_state,
        signature=signature,
        ordered_tool_results=ordered_tool_results,
        profile=profile,
    ):
        loop_state.direct_tool_requested_batch_satisfied = True

    if prefetch_predictor is not None:
        iter_tool_names = [
            str(getattr(tc, "name", "") or "").strip() for tc in tool_calls
        ]
        if prefetch_pending is not None and iter_tool_names:
            prefetch_predictor.record_outcome(prefetch_pending, iter_tool_names[0])
            prefetch_pending = None
        prefetch_predictor.observe(iter_tool_names)
        pred_tool, pred_conf = prefetch_predictor.predict(
            list(loop_state.tool_calls_made)
        )
        if pred_tool is not None and pred_conf >= float(
            profile.speculative_prefetch_threshold
        ):
            prefetch_pending = pred_tool
        loop_state.scratchpad["loop.prefetch_correct"] = prefetch_predictor.correct
        loop_state.scratchpad["loop.prefetch_wrong"] = prefetch_predictor.wrong

    loop_state.scratchpad["loop.cache_hits"] = loop_cache.hits
    loop_state.scratchpad["loop.cache_misses"] = loop_cache.misses

    loop_profiler.record_cache(
        hits=loop_cache.hits - (loop_state.scratchpad.get("_profiler_prev_hits", 0)),
        misses=loop_cache.misses
        - (loop_state.scratchpad.get("_profiler_prev_misses", 0)),
    )
    loop_state.scratchpad["_profiler_prev_hits"] = loop_cache.hits
    loop_state.scratchpad["_profiler_prev_misses"] = loop_cache.misses
    loop_profiler.record_iteration(profile.profile_name, iter_llm_duration_ms)

    _emit_iteration_event(
        loop_ctx=loop_ctx,
        profile=profile,
        loop_state=loop_state,
        llm_duration_ms=iter_llm_duration_ms,
        tool_records=iter_tool_records,
        tokens_used=iter_input_tokens + iter_output_tokens,
    )

    ctx_state = getattr(loop_ctx, "state", None)
    if ctx_state is not None and hasattr(ctx_state, STATE_KEY_MODULE_STATE):
        snapshot = LoopSnapshot(
            iteration_index=loop_state.iteration,
            message_transcript=compress_transcript(
                [
                    {
                        "role": str(getattr(message, "role", "") or ""),
                        "content": str(getattr(message, "content", "") or ""),
                    }
                    for message in loop_state.messages
                ]
            ),
            tool_call_history=[
                LoopToolCallRecord(tool_name=tc, args_hash="", result_summary="")
                for tc in loop_state.tool_calls_made
            ],
            budgets_consumed={
                "llm_calls": loop_state.llm_calls,
                "tool_calls": loop_state.total_tool_calls,
            },
            turn_scope_id=turn_scope_id,
            profile_name=profile.profile_name,
            model=model,
            allowed_tools=profile.allowed_tools or frozenset(),
            tool_results=[
                item
                for item in list(
                    loop_state.scratchpad.get("adaptive.tool_results", []) or []
                )
                if isinstance(item, dict)
            ][-24:],
        )
        module_state = dict(getattr(ctx_state, STATE_KEY_MODULE_STATE, {}) or {})
        module_state["adaptive_loop"] = snapshot.to_dict()
        ctx_state.module_state = module_state

    return prefetch_pending
