from typing import TYPE_CHECKING, Any

from openminion.modules.brain.execution.decide_contract import decide_blocker_family

from openminion.modules.brain.constants import BRAIN_DECISION_ROUTE_RESPOND
from openminion.modules.brain.diagnostics.events import CanonicalEventLogger
from openminion.modules.brain.schemas import (
    ActDecision,
    Command,
    Decision,
    RespondDecision,
    ToolCommand,
    WorkingState,
    new_uuid,
)
from openminion.modules.brain.tools.parser import (
    explicit_tool_name_sequence,
    parse_tool_command,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openminion.modules.brain.runner import BrainRunner


def _emit_decide_fail_closed_event(
    *,
    logger: CanonicalEventLogger,
    state: WorkingState,
    reason_code: str,
    source: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    detail_reason_code = str(reason_code or "").strip() or "unknown"
    blocker_family = decide_blocker_family(reason_code=detail_reason_code)
    payload: dict[str, Any] = {
        "phase": "decide",
        "fallback_mode": BRAIN_DECISION_ROUTE_RESPOND,
        "reason_code": blocker_family,
        "blocker_family": blocker_family,
        "source": source,
    }
    event_metadata = dict(metadata or {})
    if detail_reason_code != blocker_family:
        event_metadata["detail_reason_code"] = detail_reason_code
    if event_metadata:
        payload["metadata"] = event_metadata
    logger.emit(
        "brain.fail_closed.decide_invalid_output",
        payload,
        trace_id=state.trace_id,
        status="warning",
    )


def _respond_decision(
    *,
    confidence: float,
    reason_code: str,
    answer: str = "",
    sub_intents: list[str] | None = None,
) -> RespondDecision:
    return RespondDecision(
        confidence=confidence,
        reason_code=reason_code,
        respond_kind="answer",
        sub_intents=list(sub_intents or []),
        answer=answer,
    )


def _act_seeded_decision(
    *,
    confidence: float,
    reason_code: str,
    command: Command,
    sub_intents: list[str] | None = None,
    rationale: str = "",
) -> ActDecision:
    decision = ActDecision(
        confidence=confidence,
        reason_code=reason_code,
        sub_intents=list(sub_intents or []),
        rationale=rationale,
    )
    decision._seeded_commands = [command.model_copy(deep=True)]
    return decision


def _recover_simple_tool_parity_decision(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    user_input: str | None,
    capability_category: str | None,
    decision: Decision,
    response: Any | None,
    logger: CanonicalEventLogger,
    llm_call_id: str,
) -> Decision | None:
    del capability_category
    command = parse_tool_command(
        runner=runner,
        state=state,
        text=str(user_input or ""),
    )
    if command is None:
        command = _recover_seed_command_from_response(
            runner=runner,
            state=state,
            response=response,
        )
    explicit_tool_sequence = (
        ()
        if command is not None
        else explicit_tool_name_sequence(str(user_input or ""))
    )
    if command is None and not explicit_tool_sequence:
        return None
    if str(getattr(state, "tier", "") or "").strip() == "T0_direct":
        from .guards import _tier_0_restriction_decision  # noqa: PLC0415

        return _tier_0_restriction_decision(
            logger=logger,
            state=state,
            blocked_mode="act",
        )
    if str(getattr(decision, "route", "") or "").strip().lower() not in {
        "act",
        "respond",
    }:
        return None
    logger.emit(
        "brain.decision.explicit_tool_seeded",
        {
            "llm_call_id": llm_call_id,
            "tool_name": str(getattr(command, "tool_name", "") or "").strip() or None,
            "tool_sequence": list(explicit_tool_sequence) or None,
            "source_reason_code": str(
                getattr(decision, "reason_code", "") or ""
            ).strip()
            or None,
        },
        trace_id=state.trace_id,
        status="ok",
    )
    act_kwargs = {
        "confidence": float(getattr(decision, "confidence", 1.0) or 1.0),
        "sub_intents": list(getattr(decision, "sub_intents", []) or []),
        "rationale": str(getattr(decision, "rationale", "") or "").strip(),
    }
    if command is not None:
        return _act_seeded_decision(
            reason_code="explicit_tool_command",
            command=command,
            **act_kwargs,
        )
    return ActDecision(
        reason_code="explicit_tool_sequence",
        **act_kwargs,
    )


def _recover_seed_command_from_response(
    *,
    runner: "BrainRunner",
    state: WorkingState,
    response: Any | None,
) -> ToolCommand | None:
    if response is None:
        return None
    tool_calls = [
        item
        for item in list(getattr(response, "tool_calls", []) or [])
        if str(getattr(item, "name", "") or "").strip() != "submit_output"
    ]
    if len(tool_calls) != 1:
        return None
    tool_call = tool_calls[0]
    tool_name = str(getattr(tool_call, "name", "") or "").strip()
    if not tool_name:
        return None
    arguments = getattr(tool_call, "arguments", None)
    if not isinstance(arguments, dict):
        return None
    idem = runner._idempotency_key(
        session_id=state.session_id,
        trace_id=state.trace_id or "",
        text=f"tool {tool_name} {arguments!r}",
    )
    return ToolCommand(
        kind="tool",
        title=f"Tool call: {tool_name}",
        tool_name=tool_name,
        args=dict(arguments),
        success_criteria={"status": "success"},
        idempotency_key=idem or new_uuid(),
        risk_level="low",
    )


def heuristic_decision(
    runner: "BrainRunner", *, state: WorkingState, user_input: str | None
) -> Decision:
    command = parse_tool_command(
        runner=runner,
        state=state,
        text=str(user_input or ""),
    )
    if command is not None:
        return _act_seeded_decision(
            confidence=1.0,
            reason_code="explicit_tool_command",
            command=command,
        )
    return _respond_decision(
        confidence=0.0,
        reason_code="llm_unavailable",
        answer="I couldn't continue safely because no decision model was available for this turn.",
    )
