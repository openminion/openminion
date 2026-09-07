"""Runtime implementation for user-message auto-fact extraction."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ....diagnostics.events import CanonicalEventLogger
from ....retry import call_structured_with_retry
from ....schemas import UserMessageCandidateReport, WorkingState, new_uuid
from ...memory.records import _afe_config, _afe_model
from ...delegation import _runner_delegate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ....runner import BrainRunner


@dataclass(slots=True)
class _AfeConfigData:
    max_items: int
    initial_confidence: float
    model: str
    memory_service: Any
    llm_call_id: str
    hints: dict[str, Any]


def extract_user_message_candidates(
    runner: "BrainRunner",
    *,
    state: WorkingState,
    user_message: str,
    logger: CanonicalEventLogger,
) -> dict[str, Any]:
    config_data = _build_afe_config_data(
        runner=runner,
        state=state,
        user_message=user_message,
        logger=logger,
    )
    if isinstance(config_data, dict):
        return config_data
    report = _run_afe_reflection(
        runner=runner,
        state=state,
        logger=logger,
        config_data=config_data,
    )
    if isinstance(report, dict):
        return report
    return _store_afe_report(
        state=state,
        logger=logger,
        report=report,
        config_data=config_data,
    )


def _build_afe_config_data(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    user_message: str,
    logger: CanonicalEventLogger,
) -> _AfeConfigData | dict[str, Any]:
    from openminion.modules.memory.runtime.staging import (
        AFE_INITIAL_CONFIDENCE,
    )

    config = _afe_config(runner)
    enabled = bool(getattr(config, "enabled", True))
    if not enabled:
        return _emit_afe_skipped(logger=logger, state=state, reason="disabled")
    text = str(user_message or "").strip()
    min_chars = max(1, int(getattr(config, "min_user_message_chars", 8) or 8))
    if len(text) < min_chars:
        return _emit_afe_skipped(
            logger=logger,
            state=state,
            reason="user_message_too_short",
            length=len(text),
        )
    if runner.llm_api is None or runner.context_api is None:
        return _emit_afe_skipped(
            logger=logger,
            state=state,
            reason="missing_llm_or_context",
            status="warning",
        )
    if runner.memory_api is None:
        return _emit_afe_skipped(
            logger=logger,
            state=state,
            reason="memory_api_unavailable",
            status="warning",
        )
    model_tier = str(getattr(config, "model_tier", "reflect") or "reflect")
    model = _afe_model(runner, tier=model_tier)
    if not model:
        return _emit_afe_skipped(
            logger=logger,
            state=state,
            reason="missing_model",
            status="warning",
        )
    max_items = max(1, int(getattr(config, "max_items_per_turn", 5) or 5))
    initial_confidence = _resolve_initial_confidence(
        config=config, default_confidence=AFE_INITIAL_CONFIDENCE
    )
    extraction_timeout_seconds = _resolve_timeout_seconds(
        config=config, default_timeout=20
    )
    llm_call_id = new_uuid()
    logger.emit(
        "brain.auto_fact_extraction.started",
        {
            "llm_call_id": llm_call_id,
            "model": model,
            "message_chars": len(text),
        },
        trace_id=state.trace_id,
    )
    return _AfeConfigData(
        max_items=max_items,
        initial_confidence=initial_confidence,
        model=model,
        memory_service=runner.memory_api,
        llm_call_id=llm_call_id,
        hints=_afe_hints(
            text=text,
            max_items=max_items,
            llm_call_id=llm_call_id,
            extraction_timeout_seconds=extraction_timeout_seconds,
        ),
    )


def _resolve_initial_confidence(*, config: Any, default_confidence: float) -> float:
    raw_initial_confidence = getattr(config, "initial_confidence", default_confidence)
    try:
        parsed_confidence = float(raw_initial_confidence)
    except (TypeError, ValueError):
        return default_confidence
    if not 0.0 <= parsed_confidence <= 1.0:
        return default_confidence
    return parsed_confidence


def _resolve_timeout_seconds(*, config: Any, default_timeout: int) -> int:
    raw_timeout = getattr(config, "timeout_seconds", default_timeout)
    try:
        return max(1, int(raw_timeout or default_timeout))
    except (TypeError, ValueError):
        return default_timeout


def _afe_hints(
    *, text: str, max_items: int, llm_call_id: str, extraction_timeout_seconds: int
) -> dict[str, Any]:
    return {
        "_llm_call_id": llm_call_id,
        "user_input": text,
        "afe_user_message": text,
        "structured_timeout_seconds": extraction_timeout_seconds,
        "style_overrides": {
            "auto_fact_extraction_contract": (
                "Extract at most "
                f"{max_items} candidate facts, user preferences, or tasks "
                "from the user's message. Return UserMessageCandidateReport "
                "with an `items` list; do NOT emit `session_id` or "
                "`agent_id`; those are runtime-owned transport fields and "
                "will be injected after your response. "
                "Only extract things the user explicitly stated or requested "
                "do not infer or paraphrase. For each item, provide a "
                "bounded normalized_key of the form '<kind>:<slug>' where "
                "kind is one of: fact, user_preference, task. Example keys: "
                "'fact:user_name', 'user_preference:response_style', "
                "'task:deploy_auth_service'. Keep title under 200 chars and "
                "content under 600 chars. If nothing worth extracting, return "
                "an empty items list."
            )
        },
    }


def _run_afe_reflection(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    logger: CanonicalEventLogger,
    config_data: _AfeConfigData,
) -> UserMessageCandidateReport | dict[str, Any]:
    _runner_delegate(
        "_track_call_started",
        runner,
        config_data.llm_call_id,
        "reflect",
        config_data.model,
    )
    try:
        context = _runner_delegate(
            "_build_context",
            runner,
            state=state,
            purpose="reflect",
            budget={"max_tokens": min(800, state.budgets_remaining.tokens)},
            hints=config_data.hints,
            logger=logger,
        )
        raw = call_structured_with_retry(
            runner.llm_api,
            model=config_data.model,
            purpose="reflect",
            context=context,
            schema=UserMessageCandidateReport,
        )
        state.llm_calls_used += 1
        if isinstance(raw, dict):
            _runner_delegate("_debit_tokens", runner, state, raw, logger)
        return UserMessageCandidateReport.model_validate(raw)
    except Exception as exc:  # noqa: BLE001
        return _emit_afe_skipped(
            logger=logger,
            state=state,
            reason="extraction_failed",
            status="warning",
            retryable=True,
            error=str(exc),
        )
    finally:
        _runner_delegate("_track_call_completed", runner, config_data.llm_call_id)


def _store_afe_report(
    *,
    state: WorkingState,
    logger: CanonicalEventLogger,
    report: UserMessageCandidateReport,
    config_data: _AfeConfigData,
) -> dict[str, Any]:
    items = [
        {
            "kind": str(item.kind),
            "normalized_key": str(item.normalized_key),
            "title": str(item.title),
            "content": str(item.content),
            "tags": list(item.tags or ()),
            "confidence": config_data.initial_confidence,
        }
        for item in report.items[: config_data.max_items]
    ]
    envelope = {"items": items}
    state.memory_capture_report_root_turn_id = state.root_turn_id
    state.memory_capture_report = envelope
    logger.emit(
        "brain.auto_fact_extraction.completed",
        {
            "llm_call_id": config_data.llm_call_id,
            "extracted_items": len(report.items),
            "staged_candidates": 0,
            "skipped_count": 0,
            "initial_confidence": config_data.initial_confidence,
        },
        trace_id=state.trace_id,
        status="ok",
    )
    return envelope


def _emit_afe_skipped(
    *,
    logger: CanonicalEventLogger,
    state: WorkingState,
    reason: str,
    status: str = "info",
    retryable: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    logger.emit(
        "brain.auto_fact_extraction.skipped",
        {"reason": reason, **extra},
        trace_id=state.trace_id,
        status=status,
    )
    state.memory_capture_report_root_turn_id = state.root_turn_id
    state.memory_capture_report = {"items": []}
    if retryable:
        state.memory_capture_report["error_code"] = reason
    return state.memory_capture_report
