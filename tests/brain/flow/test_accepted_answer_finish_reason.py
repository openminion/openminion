"""A truncated or filtered answer is not reported as an ordinary stop.

The controls drive the real entry decision, RESPOND dispatch, meta bridge,
result conversion and metadata owner.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openminion.modules.brain.execution.dispatch import _respond_execute
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.services import RunnerExecutionServices
from openminion.modules.brain.meta import (
    MetaDirective,
    MetaMetrics,
    MetaResult,
    MetaState,
)
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.schemas import BudgetCounters, StepOutput, WorkingState
from openminion.modules.brain.schemas.freshness import (
    FreshnessContract,
    FreshnessObligations,
)
from openminion.modules.llm.schemas import LLMRequest, LLMResponse, Message, UsageInfo
from openminion.services.brain.post_execution.postprocess_response import (
    _attach_finish_reason,
)
from tests.brain.runner_test_support import _profile, fake_logger, patch

ANSWER = "Order A-104 is ready."
USER_INPUT = "Summarize the report"


class _StaticEntryLLM:
    """The entry decision's provider call, answering once with a fixed response."""

    def __init__(self, response: LLMResponse) -> None:
        self.response = response

    def estimate_tokens(self, *, model: str, context: dict) -> int:
        del model, context
        return 1

    def call(self, request: LLMRequest) -> LLMResponse:
        del request
        return self.response


def _entry_response(
    *, text: str = ANSWER, finish_reason: str = "length"
) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="test",
        model="decide-default",
        output_text=text,
        assistant_messages=[Message(role="assistant", content=text)],
        tool_calls=[],
        usage=UsageInfo(total_tokens=1),
        finish_reason=finish_reason,
    )


def _runner(response: LLMResponse) -> BrainRunner:
    return BrainRunner(
        profile=_profile(),
        session_api=_SessionAPI(),
        llm_api=_StaticEntryLLM(response),
        context_api=_ContextAPI(),
        tool_api=_ToolAPI(),
    )


class _SessionAPI:
    def list_events(self, *_args, **_kwargs) -> list:
        return []

    def append_event(self, *_args, **_kwargs) -> None:
        return None

    def append_turn(self, *_args, **_kwargs) -> None:
        return None

    def put_working_state(self, *_args, **_kwargs) -> None:
        return None


class _ContextAPI:
    def build(self, *_args, **_kwargs) -> dict:
        return {}

    def make_delta(self, **_kwargs) -> dict:
        return {"ref": "delta-1"}

    def maybe_compact(self, **_kwargs) -> None:
        return None


class _ToolAPI:
    def __init__(self) -> None:
        self.registry = SimpleNamespace(_tools={})


def _decide(response: LLMResponse, *, user_input: str = USER_INPUT):
    runner = _runner(response)
    state = WorkingState(
        session_id="finish-reason-session",
        agent_id="router-agent",
        budgets_remaining=BudgetCounters(
            ticks=10, tool_calls=5, a2a_calls=5, tokens=20000, time_ms=10000
        ),
    )
    with (
        patch.object(runner, "_estimate_tokens", return_value=1),
        patch.object(runner, "_debit_tokens", return_value=None),
    ):
        decision = runner._decide(
            state=state, user_input=user_input, logger=fake_logger()
        )
    return runner, state, decision


def _meta_override(runner: BrainRunner, directive: MetaDirective) -> None:
    runner.set_meta_override(
        "before_respond",
        MetaResult(
            meta_state=MetaState.NORMAL,
            directive=directive,
            metrics=MetaMetrics(),
        ),
    )


def _respond(
    response: LLMResponse,
    *,
    directive: MetaDirective | None = None,
    state_setup=None,
) -> tuple[ExecutionResult, StepOutput, dict[str, str]]:
    runner, state, decision = _decide(response)
    if state_setup is not None:
        state_setup(state)
    if directive is not None:
        _meta_override(runner, directive)
    ctx = ExecutionContext(
        state=state,
        decision=decision,
        user_input=USER_INPUT,
        logger=fake_logger(),
        options=runner.options,
        llm_adapter=None,
        command_executor=_NoCommands(),
        _services=RunnerExecutionServices(runner=runner),
    )
    result = _respond_execute(ctx)
    step_output = result.to_step_output()
    return result, step_output, _turn_metadata(step_output)


class _NoCommands:
    def execute(self, *_args, **_kwargs):
        raise AssertionError("the respond route executes no commands")


def _turn_metadata(
    step_output,
    *,
    tool_results_payload: list | None = None,
    termination_reason: str = "model_final",
) -> dict[str, str]:
    metadata: dict[str, str] = {}
    _attach_finish_reason(
        metadata=metadata,
        step_out=step_output,
        tool_results_payload=list(tool_results_payload or []),
        termination_reason=termination_reason,
    )
    return metadata


def test_an_unchanged_truncated_answer_reports_the_provider_termination() -> None:
    result, step_output, metadata = _respond(_entry_response(finish_reason="length"))

    assert metadata["finish_reason"] == "length"
    assert step_output.message == ANSWER
    assert result.accepted_finish_reason == "length"


def test_an_unchanged_filtered_answer_reports_the_provider_termination() -> None:
    _, _, metadata = _respond(_entry_response(finish_reason="content_filter"))

    assert metadata["finish_reason"] == "content_filter"


def test_an_ordinary_answer_still_reports_stop() -> None:
    _, _, metadata = _respond(_entry_response(finish_reason="stop"))

    assert metadata["finish_reason"] == "stop"


def test_a_stopped_override_reports_stop() -> None:
    result, step_output, metadata = _respond(
        _entry_response(finish_reason="length"),
        directive=MetaDirective(override_next_state="STOPPED", note_to_user="stop now"),
    )

    assert result.status == "stopped"
    assert step_output.message == "stop now"
    assert metadata["finish_reason"] == "stop"


def test_a_waiting_override_reports_stop() -> None:
    result, step_output, metadata = _respond(
        _entry_response(finish_reason="length"),
        directive=MetaDirective(
            override_next_state="WAITING", escalation_question="clarify now"
        ),
    )

    assert result.status == "waiting_user"
    assert step_output.message == "clarify now"
    assert metadata["finish_reason"] == "stop"


def test_a_byte_identical_respond_override_reports_stop() -> None:
    result, step_output, metadata = _respond(
        _entry_response(finish_reason="length"),
        directive=MetaDirective(override_next_state="RESPOND", note_to_user=ANSWER),
    )

    assert step_output.message == ANSWER
    assert metadata["finish_reason"] == "stop"
    assert result.accepted_finish_reason == ""


def test_a_prepended_note_reports_stop() -> None:
    _, step_output, metadata = _respond(
        _entry_response(finish_reason="length"),
        directive=MetaDirective(note_to_user="NOTE:"),
    )

    assert step_output.message == f"NOTE:\n{ANSWER}"
    assert metadata["finish_reason"] == "stop"


def test_a_freshness_rewrite_reports_stop() -> None:
    def require_live_evidence(state: WorkingState) -> None:
        state.freshness_contract = FreshnessContract(
            domain="shopping", time_sensitive=True
        )
        state.freshness_obligations = FreshnessObligations(require_live_data=True)

    result, step_output, metadata = _respond(
        _entry_response(finish_reason="length"), state_setup=require_live_evidence
    )

    assert step_output.message != ANSWER
    assert "grounded current shopping answer" in str(step_output.message)
    assert result.accepted_finish_reason == ""
    assert metadata["finish_reason"] == "stop"


def test_a_recovered_tool_parity_decision_carries_no_accepted_answer() -> None:
    _, _, decision = _decide(
        _entry_response(finish_reason="length"), user_input="tool time.now"
    )

    assert decision.route == "act"
    assert getattr(decision, "_entry_response", None) is None


def test_a_decision_without_an_accepted_answer_reports_stop() -> None:
    runner, state, decision = _decide(_entry_response(finish_reason="stop"))
    decision._entry_response = None
    ctx = ExecutionContext(
        state=state,
        decision=decision,
        user_input=USER_INPUT,
        logger=fake_logger(),
        options=runner.options,
        llm_adapter=None,
        command_executor=_NoCommands(),
        _services=RunnerExecutionServices(runner=runner),
    )

    step_output = _respond_execute(ctx).to_step_output()

    assert _turn_metadata(step_output)["finish_reason"] == "stop"


def test_an_error_status_still_wins_over_the_provider_termination() -> None:
    _, step_output, _ = _respond(_entry_response(finish_reason="length"))
    failed = ExecutionResult(
        status="failed",
        working_state=step_output.working_state,
        message=step_output.message,
        accepted_finish_reason="length",
    ).to_step_output()

    assert _turn_metadata(failed)["finish_reason"] == "error"


def test_a_tool_final_turn_keeps_its_own_reason() -> None:
    _, step_output, _ = _respond(_entry_response(finish_reason="length"))

    metadata = _turn_metadata(
        step_output,
        tool_results_payload=[{"tool_name": "file.read", "ok": True}],
        termination_reason="tool_final",
    )

    assert metadata["finish_reason"] == "tool_calls"


def test_step_output_public_schema_is_unchanged() -> None:
    """`finish_reason` is not part of the serialized StepOutput contract."""
    assert "finish_reason" not in StepOutput.model_fields
    assert "_accepted_finish_reason" not in StepOutput.model_fields

    working_state = WorkingState(
        session_id="finish-reason-session",
        agent_id="router-agent",
        budgets_remaining=BudgetCounters(
            ticks=1, tool_calls=1, a2a_calls=1, tokens=1, time_ms=1
        ),
    )
    payload = {
        "session_id": "finish-reason-session",
        "status": "done",
        "message": ANSWER,
        "working_state": working_state.model_dump(),
    }

    # It still accepts the payloads it accepted before ...
    step_output = StepOutput.model_validate(payload)
    assert step_output.model_dump() == StepOutput(**payload).model_dump()
    assert "finish_reason" not in step_output.model_dump()

    # ... and still rejects the extra field it never had.
    with pytest.raises(ValidationError) as error:
        StepOutput.model_validate({**payload, "finish_reason": None})
    assert error.value.errors()[0]["type"] == "extra_forbidden"

    step_output._accepted_finish_reason = "length"
    assert step_output.model_dump() == StepOutput(**payload).model_dump()
    assert "finish_reason" not in json.dumps(step_output.model_dump())
    assert "finish_reason" not in json.dumps(StepOutput.model_json_schema())
