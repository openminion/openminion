from typing import TYPE_CHECKING, Any

from ...diagnostics.events import CanonicalEventLogger
from ...schemas import (
    ActionResult,
    WorkingState,
)
from ...schemas.closure import ClosureJudgment
from ...runtime.memory import (
    stage_strategy_outcome,
)
from ..runtime.memory import reflection as _memory_reflection_runtime
from . import records as _memory_records
from .records import (
    _closure_intent_ids,
    _decision_card_content,
    _post_completion_critique_content,
)
from openminion.modules.memory.runtime.scope import (
    emit_write_decision,
)

_SEAM_POST_COMPLETION_CRITIQUE = (
    "brain.execution.memory.write_post_completion_critique_memory"
)

_all_steps_succeeded = _memory_records._all_steps_succeeded
_command_signatures = _memory_records._command_signatures
_dedupe_text_values = _memory_records._dedupe_text_values
_success_memory_config = _memory_records._success_memory_config
_successful_command_ids = _memory_records._successful_command_ids
_successful_tool_names = _memory_records._successful_tool_names

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...runner import BrainRunner


def write_decision_memory(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    decision: Any,
    logger: CanonicalEventLogger | None = None,
) -> list[str]:
    """Persist one typed decision-card memory record."""

    memory_api = getattr(runner, "memory_api", None)

    def _emit(event: str, payload: dict[str, Any], *, status: str = "info") -> None:
        if logger is None:
            return
        try:
            logger.emit(
                event, payload, trace_id=getattr(state, "trace_id", None), status=status
            )
        except Exception:
            return

    write_record = getattr(memory_api, "write_record", None)
    put_record = getattr(memory_api, "put_record", None)
    if memory_api is None or not (callable(write_record) or callable(put_record)):
        _emit(
            "brain.decision_memory.skipped",
            {"reason": "memory_api_unavailable"},
            status="warning",
        )
        return []

    session_id = str(getattr(state, "session_id", "") or "").strip()
    if not session_id:
        _emit(
            "brain.decision_memory.skipped",
            {"reason": "missing_session_id"},
            status="warning",
        )
        return []

    content = _decision_card_content(decision=decision, state=state)
    route = str(content.get("route_chosen", "") or "").strip() or "unknown"
    try:
        kwargs = {
            "scope": f"session:{session_id}",
            "record_type": "decision",
            "title": f"Decision: {route}",
            "content": content,
            "tags": ["decision", f"route:{route}"],
        }
        if callable(write_record):
            record_id = write_record(
                **kwargs,
                confidence=float(content.get("confidence", 0.5) or 0.5),
            )
        else:
            record_id = put_record(**kwargs)
    except Exception as exc:  # noqa: BLE001 - best-effort memory write
        _emit(
            "brain.decision_memory.skipped",
            {"reason": "write_failed", "error": str(exc)},
            status="warning",
        )
        return []

    normalized_record_id = str(record_id or "").strip()
    if not normalized_record_id:
        _emit(
            "brain.decision_memory.skipped",
            {"reason": "empty_record_id"},
            status="warning",
        )
        return []
    _emit(
        "brain.decision_memory.completed",
        {
            "record_id": normalized_record_id,
            "route": route,
            "reason_code": content.get("reason_code", ""),
        },
        status="ok",
    )
    return [normalized_record_id]


def write_post_completion_critique_memory(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    judgment: ClosureJudgment,
    logger: CanonicalEventLogger | None = None,
) -> list[str]:
    """Persist one typed post-completion critique memory record."""

    critique = getattr(judgment, "post_completion_critique", None)
    if critique is None:
        return []
    memory_api = getattr(runner, "memory_api", None)
    write_record = getattr(memory_api, "write_record", None)
    put_record = getattr(memory_api, "put_record", None)

    def _emit(event: str, payload: dict[str, Any], *, status: str = "info") -> None:
        if logger is None:
            return
        try:
            logger.emit(
                event, payload, trace_id=getattr(state, "trace_id", None), status=status
            )
        except Exception:
            return

    if memory_api is None or not (callable(write_record) or callable(put_record)):
        _emit(
            "brain.post_completion_critique.skipped",
            {"reason": "memory_api_unavailable"},
            status="warning",
        )
        return []

    valid_intent_ids = _closure_intent_ids(state)
    if valid_intent_ids and critique.intent_id not in valid_intent_ids:
        _emit(
            "brain.post_completion_critique.skipped",
            {
                "reason": "critique_link_invalid",
                "intent_id": critique.intent_id,
                "valid_intent_ids": valid_intent_ids,
            },
            status="warning",
        )
        return []

    write_scope, _event = emit_write_decision(
        runner.profile.agent_id,
        caller_seam=_SEAM_POST_COMPLETION_CRITIQUE,
    )
    content = _post_completion_critique_content(critique=critique, state=state)
    try:
        kwargs = {
            "scope": write_scope,
            "record_type": "post_completion_critique",
            "title": f"Post-completion critique: {critique.intent_id}",
            "content": content,
            "tags": [
                "post_completion_critique",
                f"intent:{critique.intent_id}",
            ],
        }
        if callable(write_record):
            record_id = write_record(**kwargs, confidence=0.7)
        else:
            record_id = put_record(**kwargs)
    except Exception as exc:  # noqa: BLE001 - best-effort memory write
        _emit(
            "brain.post_completion_critique.skipped",
            {
                "reason": "write_failed",
                "error": str(exc),
                "intent_id": critique.intent_id,
            },
            status="warning",
        )
        return []

    normalized_record_id = str(record_id or "").strip()
    if not normalized_record_id:
        _emit(
            "brain.post_completion_critique.skipped",
            {"reason": "empty_record_id", "intent_id": critique.intent_id},
            status="warning",
        )
        return []
    _emit(
        "brain.post_completion_critique.completed",
        {"record_id": normalized_record_id, "intent_id": critique.intent_id},
        status="ok",
    )
    return [normalized_record_id]


def write_strategy_outcome_memory(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    outcome_status: str,
    logger: CanonicalEventLogger | None = None,
    termination_reason: str | None = None,
) -> list[str]:
    strategy_id = str(
        getattr(state, "working_act_profile", None)
        or getattr(state, "active_mode_name", None)
        or ""
    ).strip()
    capability_category = str(
        getattr(state, "decision_capability_category", None) or ""
    ).strip()
    intent_category = str(getattr(state, "decision_reason_code", None) or "").strip()
    result = stage_strategy_outcome(
        runner,
        state=state,
        strategy_id=strategy_id,
        capability_category=capability_category,
        intent_category=intent_category,
        outcome_status=outcome_status,
        provenance_meta={
            "source_termination_reason": str(termination_reason or "").strip() or None,
        },
    )
    record_id = str(result.get("record_id") or "").strip()
    if not record_id:
        return []
    if logger is not None:
        logger.emit(
            "brain.strategy_outcome.staged",
            {
                "record_id": record_id,
                "strategy_id": strategy_id,
                "capability_category": capability_category,
                "intent_category": intent_category,
                "outcome_status": outcome_status,
                "termination_reason": str(termination_reason or "").strip(),
            },
            trace_id=state.trace_id,
            memory_refs=[record_id],
            status="ok",
        )
    return [record_id]


def extract_success_memories(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    action_result: ActionResult | None,
    judgment: ClosureJudgment,
    logger: CanonicalEventLogger,
    outcome_snapshot: dict[str, Any] | None = None,
) -> list[str]:
    return _memory_reflection_runtime.extract_success_memories(
        runner,
        state=state,
        action_result=action_result,
        judgment=judgment,
        logger=logger,
        outcome_snapshot=outcome_snapshot,
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
    return _memory_reflection_runtime.extract_failure_memories(
        runner,
        state=state,
        action_result=action_result,
        termination_reason=termination_reason,
        logger=logger,
        outcome_snapshot=outcome_snapshot,
    )


# AFE — Auto-Fact Extraction (per-turn, user-message driven)
def extract_user_message_candidates(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_message: str,
    logger: CanonicalEventLogger,
) -> list[str]:
    """AFE: Extract candidate facts/preferences/tasks from a user message."""
    return _memory_reflection_runtime.extract_user_message_candidates(
        runner,
        state=state,
        user_message=user_message,
        logger=logger,
    )
