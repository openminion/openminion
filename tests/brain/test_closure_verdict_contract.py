"""Closure judge verdict contract: satisfied and next_action must be model-authored."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from openminion.modules.brain.adapters.a2a import LocalA2AAdapter
from openminion.modules.brain.adapters.context import LocalContextAdapter
from openminion.modules.brain.adapters.llm import LlmctlAdapter
from openminion.modules.brain.adapters.llm.request import _build_request
from openminion.modules.brain.adapters.llm.runtime import _extract_structured_output
from openminion.modules.brain.adapters.memory import LocalMemoryAdapter
from openminion.modules.brain.adapters.policy import LocalPolicyAdapter
from openminion.modules.brain.adapters.session import LocalSessionStore
from openminion.modules.brain.adapters.tool import LocalToolAdapter
from openminion.modules.brain.runner import BrainRunner, RunnerOptions
from openminion.modules.brain.schemas import (
    ActionResult,
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    ClosureJudgment,
    LLMProfiles,
)
from openminion.modules.llm.schemas import LLMRequest, LLMResponse, ToolCall, UsageInfo

_GROUNDED_ANSWER = "Order A-104 is ready for pickup. Your pickup code is ZEBRA-42."
_REFUSAL = "I do not have access to your order information."


def _submit_output_response(arguments: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        ok=True,
        provider="test",
        model="judge-model",
        output_text="",
        tool_calls=[ToolCall(name="submit_output", arguments=arguments)],
        usage=UsageInfo(total_tokens=1),
        finish_reason="tool_calls",
    )


class _JudgeClient:
    def __init__(self, arguments: dict[str, Any]) -> None:
        self.arguments = arguments
        self.requests: list[LLMRequest] = []

    def call(self, req: LLMRequest) -> LLMResponse:
        self.requests.append(req)
        return _submit_output_response(self.arguments)


def _evaluate_closure(tmp: Path, client: _JudgeClient) -> ClosureJudgment:
    session = LocalSessionStore(tmp / "sessions")
    runner = BrainRunner(
        profile=AgentProfile(
            agent_id="verdict-agent",
            role="general",
            llm_profiles=LLMProfiles(
                decide_model="decide-default",
                plan_model="plan-default",
                act_model=None,
                reflect_model="judge-model",
                summarize_model="summarize-default",
            ),
            budgets=AgentBudgets(
                max_ticks_per_user_turn=3,
                max_tool_calls=2,
                max_a2a_calls=1,
                max_total_llm_tokens=4000,
                max_elapsed_ms=10000,
            ),
            defaults=AgentDefaults(),
        ),
        session_api=session,
        context_api=LocalContextAdapter(session_store=session),
        llm_api=LlmctlAdapter(client),
        tool_api=LocalToolAdapter(),
        a2a_api=LocalA2AAdapter(),
        memory_api=LocalMemoryAdapter(tmp / "memory"),
        policy_api=LocalPolicyAdapter(),
        options=RunnerOptions(metactl_enabled=False),
    )
    state = runner._load_or_init_state("closure-verdict")
    state.goal = "Check order A-104"
    return runner._evaluate_turn_closure(
        state=state,
        action_result=ActionResult(
            command_id="cmd-act-loop",
            status="success",
            summary=_GROUNDED_ANSWER,
            outputs={},
        ),
        logger=MagicMock(),
        completion_reason="act_adaptive_final_text",
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"final_answer": _REFUSAL},
        {"satisfied": True, "reason": "done", "final_answer": _REFUSAL},
        {"reason": "done", "next_action": "close", "final_answer": _REFUSAL},
    ],
    ids=["final_answer_only", "missing_next_action", "missing_satisfied"],
)
def test_closure_verdict_without_model_authored_fields_is_rejected(
    arguments: dict[str, Any],
) -> None:
    parsed = _extract_structured_output(
        _submit_output_response(arguments), ClosureJudgment
    )

    assert parsed is None


@pytest.mark.parametrize(
    ("satisfied", "next_action", "final_answer"),
    [
        (True, "close", _GROUNDED_ANSWER),
        (False, "continue", None),
        (False, "replan", None),
    ],
)
def test_closure_verdict_with_model_authored_fields_is_accepted(
    satisfied: bool, next_action: str, final_answer: str | None
) -> None:
    parsed = _extract_structured_output(
        _submit_output_response(
            {
                "satisfied": satisfied,
                "reason": "verdict",
                "next_action": next_action,
                "final_answer": final_answer,
            }
        ),
        ClosureJudgment,
    )

    assert parsed is not None
    assert parsed["satisfied"] is satisfied
    assert parsed["next_action"] == next_action
    assert parsed["final_answer"] == final_answer


def test_closure_submit_output_schema_requires_verdict_fields() -> None:
    request = _build_request(
        model="judge-model",
        purpose="judge",
        context={"hints": {"user_input": "Check order A-104"}},
        schema=ClosureJudgment,
        temperature=0.0,
    )

    submit_output = next(tool for tool in request.tools if tool.name == "submit_output")
    assert {"satisfied", "next_action"} <= set(
        submit_output.input_schema.get("required", [])
    )
    assert not {"plan_reconciliation", "verification", "review"} & set(
        submit_output.input_schema["properties"]
    )
    assert (
        _extract_structured_output(
            _submit_output_response(
                {
                    "satisfied": True,
                    "next_action": "close",
                    "plan_reconciliation": {"unreconciled_step_ids": "invalid"},
                }
            ),
            ClosureJudgment,
        )
        is None
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"final_answer": _REFUSAL},
        {"satisfied": True, "reason": "done", "final_answer": _REFUSAL},
        {"reason": "done", "next_action": "close", "final_answer": _REFUSAL},
    ],
    ids=["final_answer_only", "missing_next_action", "missing_satisfied"],
)
def test_closure_incomplete_verdict_does_not_replace_grounded_answer(
    arguments: dict[str, Any],
) -> None:
    client = _JudgeClient(arguments)

    with (
        tempfile.TemporaryDirectory() as tmp,
        pytest.raises(RuntimeError, match="LLM did not return structured output"),
    ):
        _evaluate_closure(Path(tmp), client)

    assert len(client.requests) == 1


@pytest.mark.parametrize(
    ("satisfied", "next_action", "final_answer"),
    [
        (True, "close", _GROUNDED_ANSWER),
        (False, "continue", None),
        (False, "replan", None),
    ],
)
def test_closure_preserves_complete_model_verdict(
    satisfied: bool, next_action: str, final_answer: str | None
) -> None:
    client = _JudgeClient(
        {
            "satisfied": satisfied,
            "reason": "model verdict",
            "next_action": next_action,
            "final_answer": final_answer,
        }
    )

    with tempfile.TemporaryDirectory() as tmp:
        judgment = _evaluate_closure(Path(tmp), client)

    assert judgment.satisfied is satisfied
    assert judgment.next_action == next_action
    assert judgment.final_answer == final_answer
    assert len(client.requests) == 1
    payload = judgment.model_dump(mode="json")
    assert payload["plan_reconciliation"]["state"] == "complete"
    assert payload["verification"]["signal"] == "unavailable"
    assert payload["review"]["severity"] == "unavailable"
    assert ClosureJudgment.model_validate(payload) == judgment
