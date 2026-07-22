"""Failure-memory reflection runtime helpers."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ....diagnostics.events import CanonicalEventLogger
from ....retry import call_structured_with_retry
from ....runtime.memory import apply_failure_memories
from ....schemas import ActionResult, FailureMemoryReport, WorkingState, new_uuid
from ...memory.records import (
    _dedupe_text_values,
    _successful_command_ids,
    _successful_tool_names,
)
from ...memory.reflection_common import emit_skipped, memory_barrel
from ...delegation import _runner_delegate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....runner import BrainRunner


@dataclass(slots=True)
class FailureReflectionData:
    normalized_reason: str
    command_ids: list[str]
    tool_names: list[str]
    args_signatures: list[str]
    tool_results: list[dict[str, Any]]
    error_code: str
    llm_call_id: str
    model: str
    hints: dict[str, Any]


def build_failure_reflection(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    action_result: ActionResult | None,
    termination_reason: str,
    logger: CanonicalEventLogger,
    outcome_snapshot: dict[str, Any] | None,
    strategy_outcome_refs: list[str],
) -> FailureReflectionData | list[str]:
    skip_reason = _failure_reflection_skip_reason(
        runner=runner,
        termination_reason=termination_reason,
    )
    if skip_reason is not None:
        status_kwargs = (
            {"status": "warning"}
            if skip_reason != "missing_termination_reason"
            else {}
        )
        return emit_skipped(
            logger=logger,
            event="brain.failure_memory.skipped",
            state=state,
            reason=skip_reason,
            refs=strategy_outcome_refs,
            **status_kwargs,
        )
    normalized_reason = str(termination_reason or "").strip().lower()
    snapshot = dict(outcome_snapshot or {})
    command_ids, tool_names, args_signatures, tool_results = _failure_trace_context(
        state=state,
        action_result=action_result,
        snapshot=snapshot,
    )
    if not command_ids and not tool_names and action_result is None:
        return emit_skipped(
            logger=logger,
            event="brain.failure_memory.skipped",
            state=state,
            reason="no_failure_trace",
            refs=strategy_outcome_refs,
        )
    error_code = str(
        getattr(getattr(action_result, "error", None), "code", "") or ""
    ).strip()
    llm_call_id = new_uuid()
    model = runner.profile.llm_profiles.reflect_model
    logger.emit(
        "brain.failure_memory.started",
        {
            "llm_call_id": llm_call_id,
            "model": model,
            "termination_reason": normalized_reason,
            "command_count": len(command_ids),
            "tool_names": tool_names[:5],
        },
        trace_id=state.trace_id,
    )
    return FailureReflectionData(
        normalized_reason=normalized_reason,
        command_ids=command_ids,
        tool_names=tool_names,
        args_signatures=args_signatures,
        tool_results=tool_results,
        error_code=error_code,
        llm_call_id=llm_call_id,
        model=model,
        hints=_failure_hints(
            state=state,
            action_result=action_result,
            snapshot=snapshot,
            normalized_reason=normalized_reason,
            error_code=error_code,
            command_ids=command_ids,
            tool_names=tool_names,
            args_signatures=args_signatures,
            tool_results=tool_results,
            llm_call_id=llm_call_id,
        ),
    )


def _failure_reflection_skip_reason(
    *,
    runner: "BrainRunner",
    termination_reason: str,
) -> str | None:
    if runner.llm_api is None or runner.context_api is None:
        return "missing_llm_or_context"
    if runner.memory_api is None:
        return "memory_api_unavailable"
    if not str(termination_reason or "").strip().lower():
        return "missing_termination_reason"
    return None


def _failure_trace_context(
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    snapshot: dict[str, Any],
) -> tuple[list[str], list[str], list[str], list[dict[str, Any]]]:
    command_ids = _successful_command_ids(state=state, action_result=action_result)
    tool_names = _successful_tool_names(state=state, command_ids=command_ids)
    args_signatures = memory_barrel()._command_signatures(
        state=state,
        command_ids=command_ids,
    )
    tool_results = [
        item
        for item in list(snapshot.get("tool_results", []) or [])
        if isinstance(item, dict)
    ]
    if not tool_names:
        tool_names = _text_values_from_tool_results(tool_results, "tool_name")
    if not args_signatures:
        args_signatures = _text_values_from_tool_results(tool_results, "args_signature")
    return command_ids, tool_names, args_signatures, tool_results


def _text_values_from_tool_results(
    tool_results: list[dict[str, Any]],
    key: str,
) -> list[str]:
    return _dedupe_text_values(
        [
            str(item.get(key, "") or "").strip()
            for item in tool_results
            if str(item.get(key, "") or "").strip()
        ]
    )


def _failure_hints(
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    snapshot: dict[str, Any],
    normalized_reason: str,
    error_code: str,
    command_ids: list[str],
    tool_names: list[str],
    args_signatures: list[str],
    tool_results: list[dict[str, Any]],
    llm_call_id: str,
) -> dict[str, Any]:
    return {
        "_llm_call_id": llm_call_id,
        "user_input": str(getattr(state, "goal", "") or "").strip()
        or str(getattr(state, "last_user_input", "") or "").strip(),
        "failure_memory_goal": str(getattr(state, "goal", "") or "").strip()
        or str(getattr(getattr(state, "plan", None), "objective", "") or "").strip(),
        "failure_memory_termination_reason": normalized_reason,
        "failure_memory_error_code": error_code,
        "failure_memory_summary": str(
            getattr(action_result, "summary", "") or ""
        ).strip(),
        "failure_memory_command_ids": command_ids,
        "failure_memory_tool_names": tool_names,
        "failure_memory_args_signatures": args_signatures,
        "failure_memory_step_outputs": [
            item.model_dump(mode="json")
            for item in list(getattr(state, "step_outputs", []) or [])
        ],
        "failure_memory_tool_results": tool_results,
        "failure_memory_correction_history": list(
            snapshot.get("correction_history", []) or []
        ),
        "failure_memory_loop_iteration": int(snapshot.get("loop_iteration", 0) or 0),
        "style_overrides": {
            "failure_memory_contract": (
                "Extract only reusable failure-path guidance from this terminated run. "
                "Return FailureMemoryReport with zero or more correction items and an "
                "optional meta_rule_preference. Allowed durable item kind is correction only. "
                "Use agent scope. Keep confidence realistic. Preserve SRTF by keeping "
                "negative tool_outcome as operational history rather than prompt guidance."
            )
        },
    }


def run_failure_reflection(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    data: FailureReflectionData,
    strategy_outcome_refs: list[str],
) -> FailureMemoryReport | list[str]:
    try:
        context = _runner_delegate(
            "_build_context",
            runner,
            state=state,
            purpose="reflect",
            budget={"max_tokens": min(1500, state.budgets_remaining.tokens)},
            hints=data.hints,
            logger=logger,
        )
        raw = call_structured_with_retry(
            runner.llm_api,
            model=data.model,
            purpose="reflect",
            context=context,
            schema=FailureMemoryReport,
        )
        state.llm_calls_used += 1
        if isinstance(raw, dict):
            _runner_delegate("_debit_tokens", runner, state, raw, logger)
        return FailureMemoryReport.model_validate(raw)
    except Exception as exc:
        return emit_skipped(
            logger=logger,
            event="brain.failure_memory.skipped",
            state=state,
            reason="extraction_failed",
            refs=strategy_outcome_refs,
            status="warning",
            error=str(exc),
        )


def complete_failure_reflection(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    report: FailureMemoryReport,
    data: FailureReflectionData,
    strategy_outcome_refs: list[str],
) -> list[str]:
    write_result = apply_failure_memories(
        runner,
        state=state,
        report=report,
        logger=logger,
        provenance_meta={
            "source_trace_id": state.trace_id,
            "source_command_ids": report.command_ids or data.command_ids,
            "source_tool_names": data.tool_names,
            "source_args_signatures": data.args_signatures,
            "source_termination_reason": data.normalized_reason,
            "source_error_code": data.error_code or None,
        },
    )
    candidate_ids = list(write_result.get("candidate_ids", []) or [])
    preference_candidate_id = write_result.get("meta_rule_preference_candidate_id")
    logger.emit(
        "brain.failure_memory.completed",
        {
            "llm_call_id": data.llm_call_id,
            "command_ids": (report.command_ids or data.command_ids)[:10],
            "termination_reason": data.normalized_reason,
            "item_count": len(report.items),
            "candidate_count": len(candidate_ids),
            "candidate_sample": candidate_ids[:5],
            "meta_rule_preference_candidate_id": preference_candidate_id,
            "meta_rule_preference_skipped_reason": write_result.get(
                "meta_rule_preference_skipped_reason"
            ),
        },
        trace_id=state.trace_id,
        memory_refs=strategy_outcome_refs
        + candidate_ids
        + ([str(preference_candidate_id)] if preference_candidate_id else []),
        status="ok",
    )
    if preference_candidate_id:
        candidate_ids.append(str(preference_candidate_id))
    return strategy_outcome_refs + candidate_ids
