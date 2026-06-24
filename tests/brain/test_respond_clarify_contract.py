from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.execution.dispatch import invoke_decision_direct
from openminion.modules.brain.schemas import (
    BudgetCounters,
    RespondDecision,
    StepOutput,
    WorkingState,
)


@dataclass
class _FakeServices:
    last_output: StepOutput | None = None

    def save_state(self, *, state: WorkingState) -> None:
        del state

    def emit_phase_status(self, *, state: WorkingState, **kwargs) -> None:
        del state, kwargs

    def respond_with_meta(
        self,
        *,
        state: WorkingState,
        logger: Any,
        message: str,
        status: str,
        action_result=None,
    ) -> StepOutput:
        del logger, action_result
        output = StepOutput(
            session_id=state.session_id,
            status=status,
            message=message,
            working_state=state,
            action_result=None,
        )
        self.last_output = output
        return output

    def direct_response(self, *, user_input, decision):
        del user_input
        if getattr(decision, "respond_kind", "") == "clarify":
            return decision.question
        return decision.answer


def _state() -> WorkingState:
    return WorkingState(
        session_id="s-respond",
        agent_id="agent",
        budgets_remaining=BudgetCounters(
            ticks=5,
            tool_calls=5,
            a2a_calls=5,
            tokens=1000,
            time_ms=10_000,
        ),
    )


def _runner(services: _FakeServices) -> SimpleNamespace:
    return SimpleNamespace(
        profile=None,
        options=SimpleNamespace(),
        llm_api=None,
        _emit_phase_status=lambda **kwargs: None,
        _respond_with_meta=services.respond_with_meta,
        _direct_response=services.direct_response,
    )


def test_respond_mode_waits_for_user_on_clarify() -> None:
    decision = RespondDecision(
        confidence=0.9,
        reason_code="clarify",
        respond_kind="clarify",
        question="Which city?",
    )
    services = _FakeServices()

    result = invoke_decision_direct(
        _runner(services),
        state=_state(),
        decision=decision,
        user_input="hello",
        logger=SimpleNamespace(emit=lambda *args, **kwargs: None),
    )

    assert result.status == "waiting_user"
    assert services.last_output is not None
    assert services.last_output.status == "waiting_user"
    assert services.last_output.message == "Which city?"
