"""Turn-recursive runtime implementation for autonomous execution."""

from typing import TYPE_CHECKING, Any

from openminion.base.config.env import resolve_environment_config

from ....constants import (
    BRAIN_ACTION_STATUS_FAILED,
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_DISPOSITION_CLOSE,
    BRAIN_STATE_DONE,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_WAITING_USER,
)
from ....diagnostics.events import CanonicalEventLogger
from ....diagnostics.transitions import transition
from ....schemas import ActionError, ActionResult, WorkingState
from ...closure import final_close_message
from ...memory import extract_success_memories
from ...delegation import _runner_delegate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....runner import BrainRunner


def run_recursive_turn(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_input: str | None,
    logger: CanonicalEventLogger,
) -> Any:
    """Delegate to openminion-rlm for autonomous-mode turns."""
    try:
        recursive_source = _recursive_source(runner)
        blocked = _maybe_block_recursive_turn(
            runner=runner,
            state=state,
            logger=logger,
            recursive_source=recursive_source,
        )
        if blocked is not None:
            return blocked
        logger.emit(
            "brain.recursive_turn.started",
            {"query": user_input or state.goal, "source": recursive_source},
            trace_id=state.trace_id,
        )
        result = runner.rlm_api.generate(
            session_id=state.session_id,
            agent_id=state.agent_id,
            purpose="act",
            query=user_input or state.goal or "",
            ts=_turn_settings(runner, state),
            budgets=_recursive_budgets(state),
        )
        _debit_recursive_budgets(state=state, result=result)
        _transition_after_recursive_result(state=state, logger=logger, result=result)
        action_result = _build_recursive_action_result(
            runner=runner, state=state, logger=logger, result=result
        )
        close_message = _finalize_recursive_result(
            runner=runner,
            state=state,
            logger=logger,
            result=result,
            action_result=action_result,
        )
        logger.emit(
            "brain.recursive_turn.completed",
            {
                "ticks": result.get("ticks_used"),
                "reason": result.get("stop_reason"),
                "source": recursive_source,
            },
            trace_id=state.trace_id,
        )
        return _runner_delegate(
            "_respond_with_meta",
            runner,
            state=state,
            logger=logger,
            message=close_message,
            status=state.status,
            action_result=action_result,
        )
    except Exception as exc:
        return _recursive_runtime_error(
            runner=runner, state=state, logger=logger, exc=exc
        )


def _recursive_source(runner: "BrainRunner") -> str:
    return (
        str(getattr(runner.rlm_api, "recursive_source", "unknown") or "unknown")
        .strip()
        .lower()
    )


def _maybe_block_recursive_turn(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    recursive_source: str,
) -> Any | None:
    require_real_rlm = str(
        resolve_environment_config().get("OPENMINION_BRAIN_REQUIRE_REAL_RLM", "0")
    ).strip().lower() in {"1", "true", "yes", "on"}
    if not require_real_rlm or recursive_source == "real_rlm":
        return None
    logger.emit(
        "brain.recursive_turn.blocked",
        {"reason": "real_rlm_required", "source": recursive_source},
        trace_id=state.trace_id,
    )
    transition(state, "rlm_unavailable", logger=logger)
    return _runner_delegate(
        "_respond_with_meta",
        runner,
        state=state,
        logger=logger,
        message=(
            "Autonomous recursive execution requires a real RLM backend in "
            "this runtime. Please reconfigure RLM or continue in guided mode."
        ),
        status=BRAIN_STATE_WAITING_USER,
        action_result=ActionResult(
            command_id="rlm_turn",
            status=BRAIN_ACTION_STATUS_NEEDS_USER,
            summary="real_rlm_required",
            error=ActionError(
                code="REAL_RLM_REQUIRED",
                message=(
                    "Recursive autonomous mode is blocked because only "
                    "local/mock RLM is available."
                ),
            ),
        ),
    )


def _turn_settings(runner: "BrainRunner", state: WorkingState) -> dict[str, Any]:
    meta_cfg = getattr(runner.options, "metactl_config", None)
    verification_mode = (
        "none"
        if meta_cfg is None
        else str(
            getattr(
                meta_cfg,
                "high_risk_verification_mode",
                getattr(meta_cfg, "verification_mode", "none"),
            )
        )
    )
    return {
        "retry_count": state.replans_used,
        "verification_mode": verification_mode,
        "budget_tier": "normal",
        "invariants": [],
    }


def _recursive_budgets(state: WorkingState) -> dict[str, int]:
    return {
        "max_ticks": state.budgets_remaining.ticks,
        "max_tool_calls": state.budgets_remaining.tool_calls,
        "max_output_tokens": min(700, state.budgets_remaining.tokens),
    }


def _debit_recursive_budgets(*, state: WorkingState, result: dict[str, Any]) -> None:
    state.budgets_remaining.ticks = max(
        0, state.budgets_remaining.ticks - int(result.get("ticks_used", 1))
    )
    state.budgets_remaining.tokens = max(
        0, state.budgets_remaining.tokens - int(result.get("total_output_tokens", 0))
    )


def _transition_after_recursive_result(
    *, state: WorkingState, logger: CanonicalEventLogger, result: dict[str, Any]
) -> None:
    transition(
        state,
        "task_completed"
        if result.get("stop_reason") == "completed"
        else "budget_exhausted",
        logger=logger,
    )


def _build_recursive_action_result(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    result: dict[str, Any],
) -> ActionResult:
    memory_refs = _store_recursive_write_intents(
        runner=runner, state=state, logger=logger, result=result
    )
    artifact_refs = [event.get("ref", "") for event in result.get("evidence_refs", [])]
    return ActionResult(
        command_id="rlm_turn",
        status=BRAIN_ACTION_STATUS_SUCCESS
        if result.get("stop_reason") == "completed"
        else BRAIN_ACTION_STATUS_NEEDS_USER,
        summary=result.get("final_text", ""),
        outputs=result.get("structured_output") or {},
        artifact_refs=[ref for ref in artifact_refs if ref],
        memory_refs=memory_refs,
    )


def _store_recursive_write_intents(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    result: dict[str, Any],
) -> list[str]:
    memory_refs: list[str] = []
    for intent in result.get("write_intents", []):
        try:
            memory_id = runner.memory_api.store(
                session_id=state.session_id,
                agent_id=state.agent_id,
                text=intent.get("content", ""),
                record_type=intent.get("intent_type", "fact"),
                ttl_days=30,
            )
            if memory_id:
                memory_refs.append(memory_id)
        except Exception as exc:
            logger.emit(
                "brain.recursive_turn.writeback_error",
                {"error": str(exc)},
                trace_id=state.trace_id,
            )
    return memory_refs


def _finalize_recursive_result(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    result: dict[str, Any],
    action_result: ActionResult,
) -> str:
    if state.status != BRAIN_STATE_DONE:
        return result.get("final_text") or "Completed recursive turn."
    judgment = _runner_delegate(
        "_evaluate_turn_closure",
        runner,
        state=state,
        action_result=action_result,
        logger=logger,
        completion_reason="recursive_turn_completed",
    )
    disposition = _runner_delegate(
        "_apply_closure_judgment", runner, state=state, judgment=judgment
    )
    if disposition != BRAIN_DISPOSITION_CLOSE:
        if state.status == "active":
            transition(state, "checkpoint_reached", logger=logger)
        return (
            judgment.reason
            or result.get("final_text")
            or "I need more input to continue."
        )
    extract_success_memories(
        runner,
        state=state,
        action_result=action_result,
        judgment=judgment,
        logger=logger,
    )
    return final_close_message(
        state=state,
        judgment=judgment,
        action_result=action_result,
        fallback_message=result.get("final_text") or "Completed recursive turn.",
    )


def _recursive_runtime_error(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    exc: Exception,
) -> Any:
    logger.emit(
        "brain.recursive_turn.error",
        {"error": str(exc), "source": _recursive_source(runner)},
        trace_id=state.trace_id,
    )
    transition(state, "fatal_error", logger=logger)
    return _runner_delegate(
        "_respond_with_meta",
        runner,
        state=state,
        logger=logger,
        message=f"Recursive mode error: {exc}",
        status=BRAIN_STATE_ERROR,
        action_result=ActionResult(
            command_id="rlm_turn",
            status=BRAIN_ACTION_STATUS_FAILED,
            error=ActionError(code="RLM_ERROR", message=str(exc)),
        ),
    )
