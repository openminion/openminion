"""Runtime implementations for execution memory reflection flows."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ....diagnostics.events import CanonicalEventLogger
from ....retry import call_structured_with_retry
from ....runtime.memory import apply_success_memories
from ....schemas import (
    ActionResult,
    SuccessMemoryReport,
    WorkingState,
    new_uuid,
)
from ....schemas.closure import ClosureJudgment
from ...memory.records import (
    _all_steps_succeeded,
    _dedupe_text_values,
    _latest_trace_context,
    _success_memory_config,
    _successful_command_ids,
    _successful_tool_names,
    _thinking_excerpt_from_trace_context,
)
from . import afe as _afe_runtime
from . import failure_reflection as _failure_runtime
from ...memory.reflection_common import emit_skipped, memory_barrel
from ...delegation import _runner_delegate
from openminion.base.constants import STATE_KEY_SOURCE_OUTCOME

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....runner import BrainRunner


@dataclass(slots=True)
class _SuccessReflectionData:
    command_ids: list[str]
    tool_names: list[str]
    artifact_refs: list[str]
    snapshot: dict[str, Any]
    snapshot_refs: list[str]
    llm_call_id: str
    model: str
    source_thinking_rationale: str
    hints: dict[str, Any]


def extract_success_memories(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    judgment: ClosureJudgment,
    logger: CanonicalEventLogger,
    outcome_snapshot: dict[str, Any] | None = None,
) -> list[str]:
    config = _success_memory_config(runner)
    strategy_outcome_refs: list[str] = []
    skipped = _validate_success_preconditions(
        runner=runner,
        state=state,
        action_result=action_result,
        judgment=judgment,
        logger=logger,
        config=config,
        strategy_outcome_refs=strategy_outcome_refs,
    )
    if skipped is not None:
        return skipped
    strategy_outcome_refs = _memory_barrel().write_strategy_outcome_memory(
        runner,
        state=state,
        outcome_status="success",
        logger=logger,
    )
    reflection = _build_success_reflection(
        runner=runner,
        state=state,
        action_result=action_result,
        judgment=judgment,
        logger=logger,
        outcome_snapshot=outcome_snapshot,
        strategy_outcome_refs=strategy_outcome_refs,
    )
    if isinstance(reflection, list):
        return reflection
    report = _run_success_reflection(
        runner=runner,
        state=state,
        logger=logger,
        data=reflection,
        strategy_outcome_refs=strategy_outcome_refs,
    )
    if isinstance(report, list):
        return report
    return _complete_success_reflection(
        runner=runner,
        state=state,
        judgment=judgment,
        logger=logger,
        report=report,
        data=reflection,
        strategy_outcome_refs=strategy_outcome_refs,
    )


def extract_failure_memories(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    termination_reason: str,
    logger: CanonicalEventLogger,
    outcome_snapshot: dict[str, Any] | None = None,
) -> list[str]:
    strategy_outcome_refs = _memory_barrel().write_strategy_outcome_memory(
        runner,
        state=state,
        outcome_status="failure",
        logger=logger,
        termination_reason=termination_reason,
    )
    reflection = _failure_runtime.build_failure_reflection(
        runner=runner,
        state=state,
        action_result=action_result,
        termination_reason=termination_reason,
        logger=logger,
        outcome_snapshot=outcome_snapshot,
        strategy_outcome_refs=strategy_outcome_refs,
    )
    if isinstance(reflection, list):
        return reflection
    report = _failure_runtime.run_failure_reflection(
        runner=runner,
        state=state,
        logger=logger,
        data=reflection,
        strategy_outcome_refs=strategy_outcome_refs,
    )
    if isinstance(report, list):
        return report
    return _failure_runtime.complete_failure_reflection(
        runner=runner,
        state=state,
        logger=logger,
        report=report,
        data=reflection,
        strategy_outcome_refs=strategy_outcome_refs,
    )


def extract_user_message_candidates(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_message: str,
    logger: CanonicalEventLogger,
) -> list[str]:
    return _afe_runtime.extract_user_message_candidates(
        runner=runner,
        state=state,
        user_message=user_message,
        logger=logger,
    )


def _validate_success_preconditions(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    action_result: ActionResult | None,
    judgment: ClosureJudgment,
    logger: CanonicalEventLogger,
    config: Any,
    strategy_outcome_refs: list[str],
) -> list[str] | None:
    if config is None or not bool(getattr(config, "enabled", False)):
        return emit_skipped(
            logger=logger,
            event="brain.success_memory.skipped",
            state=state,
            reason="disabled",
            refs=strategy_outcome_refs,
        )
    if (
        bool(getattr(config, "require_closure_satisfied", True))
        and not judgment.satisfied
    ):
        return emit_skipped(
            logger=logger,
            event="brain.success_memory.skipped",
            state=state,
            reason="closure_not_satisfied",
            refs=strategy_outcome_refs,
        )
    if str(getattr(judgment, "next_action", "") or "").strip().lower() != "close":
        return emit_skipped(
            logger=logger,
            event="brain.success_memory.skipped",
            state=state,
            reason="non_close_disposition",
            refs=strategy_outcome_refs,
        )
    if (
        action_result is None
        or str(getattr(action_result, "status", "") or "").strip().lower() != "success"
    ):
        return emit_skipped(
            logger=logger,
            event="brain.success_memory.skipped",
            state=state,
            reason="non_success_action_result",
            refs=strategy_outcome_refs,
        )
    if bool(
        getattr(config, "require_all_steps_successful", False)
    ) and not _all_steps_succeeded(state):
        return emit_skipped(
            logger=logger,
            event="brain.success_memory.skipped",
            state=state,
            reason="all_steps_success_required",
            refs=strategy_outcome_refs,
        )
    if runner.llm_api is None or runner.context_api is None:
        return emit_skipped(
            logger=logger,
            event="brain.success_memory.skipped",
            state=state,
            reason="missing_llm_or_context",
            refs=strategy_outcome_refs,
            status="warning",
        )
    if runner.memory_api is None:
        return emit_skipped(
            logger=logger,
            event="brain.success_memory.skipped",
            state=state,
            reason="memory_api_unavailable",
            refs=strategy_outcome_refs,
            status="warning",
        )
    return None


def _build_success_reflection(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    action_result: ActionResult | None,
    judgment: ClosureJudgment,
    logger: CanonicalEventLogger,
    outcome_snapshot: dict[str, Any] | None,
    strategy_outcome_refs: list[str],
) -> _SuccessReflectionData | list[str]:
    command_ids = _successful_command_ids(state=state, action_result=action_result)
    tool_names = _successful_tool_names(state=state, command_ids=command_ids)
    artifact_refs = _dedupe_text_values(
        [
            artifact_ref
            for item in list(getattr(state, "step_outputs", []) or [])
            for artifact_ref in list(getattr(item, "artifact_refs", []) or [])
        ]
    )
    if not command_ids and not artifact_refs:
        return emit_skipped(
            logger=logger,
            event="brain.success_memory.skipped",
            state=state,
            reason="no_success_trace",
            refs=strategy_outcome_refs,
        )
    snapshot = dict(outcome_snapshot or {})
    snapshot_refs = _dedupe_text_values(
        list(snapshot.get("decision_memory_refs", []) or [])
        or list(getattr(state, "decision_memory_refs", []) or [])
    )
    llm_call_id = new_uuid()
    model = runner.profile.llm_profiles.reflect_model
    logger.emit(
        "brain.success_memory.started",
        {
            "llm_call_id": llm_call_id,
            "model": model,
            "command_count": len(command_ids),
            "tool_names": tool_names[:5],
        },
        trace_id=state.trace_id,
    )
    hints = _success_hints(
        runner=runner,
        state=state,
        judgment=judgment,
        command_ids=command_ids,
        tool_names=tool_names,
        artifact_refs=artifact_refs,
        snapshot=snapshot,
        snapshot_refs=snapshot_refs,
        llm_call_id=llm_call_id,
    )
    return _SuccessReflectionData(
        command_ids=command_ids,
        tool_names=tool_names,
        artifact_refs=artifact_refs,
        snapshot=snapshot,
        snapshot_refs=snapshot_refs,
        llm_call_id=llm_call_id,
        model=model,
        source_thinking_rationale=str(
            hints.get("success_memory_thinking_excerpt", "") or ""
        ).strip(),
        hints=hints,
    )


def _success_hints(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    judgment: ClosureJudgment,
    command_ids: list[str],
    tool_names: list[str],
    artifact_refs: list[str],
    snapshot: dict[str, Any],
    snapshot_refs: list[str],
    llm_call_id: str,
) -> dict[str, Any]:
    return {
        "_llm_call_id": llm_call_id,
        "user_input": str(getattr(state, "goal", "") or "").strip()
        or str(getattr(state, "last_user_input", "") or "").strip(),
        "success_memory_goal": str(getattr(state, "goal", "") or "").strip()
        or str(getattr(getattr(state, "plan", None), "objective", "") or "").strip(),
        "success_memory_closure_reason": str(
            getattr(judgment, "reason", "") or ""
        ).strip(),
        "success_memory_next_action": str(
            getattr(judgment, "next_action", "") or ""
        ).strip(),
        "success_memory_command_ids": command_ids,
        "success_memory_tool_names": tool_names,
        "success_memory_step_outputs": [
            item.model_dump(mode="json")
            for item in list(getattr(state, "step_outputs", []) or [])
        ],
        "success_memory_artifact_refs": artifact_refs,
        "success_memory_outcome_refs": snapshot_refs,
        "success_memory_thinking_excerpt": _thinking_excerpt_from_trace_context(
            _latest_trace_context(runner)
        ),
        "success_memory_context_pack_version": str(
            snapshot.get("decision_context_pack_version")
            or getattr(state, "decision_context_pack_version", "")
            or ""
        ).strip(),
        "success_memory_context_recorded_at": str(
            snapshot.get("decision_context_recorded_at")
            or getattr(state, "decision_context_recorded_at", "")
            or ""
        ).strip(),
        "style_overrides": {
            "success_memory_contract": (
                "Extract only reusable success-path memories from this completed run. "
                "Return SuccessMemoryReport with zero or more items. "
                "Allowed item kinds are procedure and tool_habit only. "
                "Prefer reusable execution habits over one-off facts. "
                "Use agent scope. Keep confidence realistic. When a thinking excerpt "
                "is present, carry the most relevant bounded excerpt into item.rationale."
            )
        },
    }


def _run_success_reflection(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    data: _SuccessReflectionData,
    strategy_outcome_refs: list[str],
) -> SuccessMemoryReport | list[str]:
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
            schema=SuccessMemoryReport,
        )
        state.llm_calls_used += 1
        if isinstance(raw, dict):
            _runner_delegate("_debit_tokens", runner, state, raw, logger)
        return SuccessMemoryReport.model_validate(raw)
    except Exception as exc:
        return emit_skipped(
            logger=logger,
            event="brain.success_memory.skipped",
            state=state,
            reason="extraction_failed",
            refs=strategy_outcome_refs,
            status="warning",
            error=str(exc),
        )


def _complete_success_reflection(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    judgment: ClosureJudgment,
    logger: CanonicalEventLogger,
    report: SuccessMemoryReport,
    data: _SuccessReflectionData,
    strategy_outcome_refs: list[str],
) -> list[str]:
    write_result = apply_success_memories(
        runner,
        state=state,
        report=report,
        logger=logger,
        provenance_meta={
            STATE_KEY_SOURCE_OUTCOME: "success",
            "source_trace_id": state.trace_id,
            "source_command_ids": report.command_ids or data.command_ids,
            "source_closure_reason": judgment.reason,
            "source_context_pack_version": str(
                data.snapshot.get("decision_context_pack_version")
                or getattr(state, "decision_context_pack_version", "")
                or ""
            ).strip()
            or None,
            "source_context_recorded_at": str(
                data.snapshot.get("decision_context_recorded_at")
                or getattr(state, "decision_context_recorded_at", "")
                or ""
            ).strip()
            or None,
            "source_outcome_record_ids": data.snapshot_refs,
            "source_thinking_rationale": data.source_thinking_rationale or None,
        },
    )
    candidate_ids = list(write_result.get("candidate_ids", []) or [])
    skipped_items = list(write_result.get("skipped_items", []) or [])
    logger.emit(
        "brain.success_memory.completed",
        {
            "llm_call_id": data.llm_call_id,
            "command_ids": (report.command_ids or data.command_ids)[:10],
            "item_count": len(report.items),
            "candidate_count": len(candidate_ids),
            "candidate_sample": candidate_ids[:5],
            "skipped_count": len(skipped_items),
        },
        trace_id=state.trace_id,
        memory_refs=strategy_outcome_refs + candidate_ids,
        status="ok",
    )
    return strategy_outcome_refs + candidate_ids


def _memory_barrel() -> Any:
    return memory_barrel()
