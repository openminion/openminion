from __future__ import annotations

from typing import Any

import pytest

from openminion.modules.brain.retry import (
    STRUCTURED_FAILURE_KIND_KEY,
    STRUCTURED_HAS_TOOL_CALLS_KEY,
    STRUCTURED_RETRYABLE_KEY,
    build_structured_retry_message,
    call_structured_with_retry,
)
from openminion.modules.brain.schemas.closure import ClosureJudgment
from openminion.modules.brain.schemas import DecisionAdapter, Plan
from openminion.modules.brain.schemas.state import PostActionJudgment


class _FakeLLM:
    contract_version = "v1"

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def estimate_tokens(self, *, model: str, context: dict[str, Any]) -> int:
        del model, context
        return 1

    def call_structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _retryable_decide_result() -> dict[str, Any]:
    return {
        "mode": "respond",
        "confidence": 0.3,
        "reason_code": "invalid_decide_tool_call",
        "respond_kind": "clarify",
        "question": "retry me",
        STRUCTURED_RETRYABLE_KEY: True,
        STRUCTURED_FAILURE_KIND_KEY: "invalid_decide_tool_call",
        STRUCTURED_HAS_TOOL_CALLS_KEY: True,
    }


def _full_decision(mode: str = "respond") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": mode,
        "confidence": 0.9,
        "reason_code": "test",
        "sub_intents": [],
        "rationale": "",
    }
    if mode == "respond":
        payload["respond_kind"] = "answer"
        payload["answer"] = "hello"
    elif mode == "respond.clarify":
        payload["mode"] = "respond"
        payload["respond_kind"] = "clarify"
        payload["question"] = "clarify?"
    elif mode == "plan":
        payload["plan_strategy"] = "sequential"
        payload["plan_hint"] = "plan it"
    elif mode == "act":
        payload["mode"] = "act"
        payload["act_profile"] = "general"
        payload["execution_target"] = {"kind": "local"}
    return payload


def _simplified_decision(mode: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": mode,
        "confidence": 0.7,
        "reason_code": "simplified",
    }
    if mode == "respond":
        payload["respond_kind"] = "answer"
        payload["answer"] = "hello"
    elif mode == "respond.clarify":
        payload["mode"] = "respond"
        payload["respond_kind"] = "clarify"
        payload["question"] = "clarify?"
    elif mode == "plan":
        payload["plan_strategy"] = "sequential"
        payload["plan_hint"] = "plan it"
    elif mode == "act":
        payload["act_profile"] = "general"
        payload["execution_target"] = {"kind": "local"}
    return payload


def _ultra_decision(mode: str, detail: str = "hello") -> dict[str, Any]:
    return {"mode": mode, "detail": detail}


def test_same_schema_retry_uses_identical_schema() -> None:
    llm = _FakeLLM([_retryable_decide_result(), _full_decision("respond")])

    result = call_structured_with_retry(
        llm,
        model="claude-3-5-sonnet",
        purpose="decide",
        context={"messages": [], "hints": {}},
        schema=DecisionAdapter,
    )

    assert result["route"] == "respond"
    assert [call["schema"].__name__ for call in llm.calls] == ["Decision", "Decision"]


def test_progressive_simplification_uses_level_sequence() -> None:
    llm = _FakeLLM([_retryable_decide_result(), _simplified_decision("respond")])

    result = call_structured_with_retry(
        llm,
        model="gpt-4o-2024-08-06",
        purpose="decide",
        context={"messages": [], "hints": {}},
        schema=DecisionAdapter,
    )

    assert result["route"] == "respond"
    assert [call["schema"].__name__ for call in llm.calls] == [
        "Decision",
        "SimplifiedDecision",
    ]


def test_progressive_simplification_round_trips_respond() -> None:
    llm = _FakeLLM([_retryable_decide_result(), _simplified_decision("respond")])

    result = call_structured_with_retry(
        llm,
        model="gpt-4o-mini",
        purpose="decide",
        context={"messages": [], "hints": {}},
        schema=DecisionAdapter,
    )

    assert result["route"] == "respond"
    assert result["answer"] == "hello"


def test_progressive_simplification_round_trips_respond_clarify() -> None:
    llm = _FakeLLM(
        [_retryable_decide_result(), _simplified_decision("respond.clarify")]
    )

    result = call_structured_with_retry(
        llm,
        model="gpt-4o-mini",
        purpose="decide",
        context={"messages": [], "hints": {}},
        schema=DecisionAdapter,
    )

    assert result["route"] == "respond"
    assert result["respond_kind"] == "clarify"
    assert result["question"] == "clarify?"


def test_progressive_simplification_round_trips_act_orchestrate() -> None:
    llm = _FakeLLM([_retryable_decide_result(), _simplified_decision("act")])

    result = call_structured_with_retry(
        llm,
        model="gpt-4o-mini",
        purpose="decide",
        context={"messages": [], "hints": {}},
        schema=DecisionAdapter,
    )

    assert result["route"] == "act"
    assert result["act_profile"] == "general"


def test_progressive_simplification_action_mode_round_trips_to_act() -> None:
    llm = _FakeLLM(
        [
            _retryable_decide_result(),
            {"mode": "act", "confidence": 0.7, "reason_code": "simplified"},
            _ultra_decision("act", "run weather"),
        ]
    )

    result = call_structured_with_retry(
        llm,
        model="gpt-4o-mini",
        purpose="decide",
        context={"messages": [], "hints": {}},
        schema=DecisionAdapter,
    )

    assert result["route"] == "act"
    assert result["reason_code"] == "simplified"
    assert [call["schema"].__name__ for call in llm.calls] == [
        "Decision",
        "SimplifiedDecision",
    ]


def test_max_structured_retries_controls_attempt_count() -> None:
    llm = _FakeLLM([_retryable_decide_result()])

    result = call_structured_with_retry(
        llm,
        model="gpt-4o-mini",
        purpose="decide",
        context={
            "messages": [],
            "hints": {
                "model_capability_overrides": {
                    "gpt4_default": {"max_structured_retries": 1}
                }
            },
        },
        schema=DecisionAdapter,
    )

    assert result["route"] == "respond"
    assert len(llm.calls) == 1


def test_retry_nudge_style_appends_distinct_guidance() -> None:
    default_message = build_structured_retry_message(
        schema_name="Decision",
        has_prior_results=False,
    )
    openai_message = build_structured_retry_message(
        schema_name="Decision",
        has_prior_results=False,
        retry_nudge_style="openai_function_calling",
    )
    json_body_message = build_structured_retry_message(
        schema_name="Decision",
        has_prior_results=False,
        retry_nudge_style="json_body_first",
    )

    assert "Return only the structured JSON object" not in default_message
    assert "Return only the structured JSON object" in openai_message
    assert "put the JSON object directly in the response body" in json_body_message
    assert "execution_target.kind='delegated'" in default_message


def test_non_decision_schema_remains_same_schema() -> None:
    invalid = {
        STRUCTURED_RETRYABLE_KEY: True,
        STRUCTURED_FAILURE_KIND_KEY: "invalid_structured_output",
    }
    llm = _FakeLLM([invalid, invalid])

    with pytest.raises(RuntimeError, match="structured output"):
        call_structured_with_retry(
            llm,
            model="gpt-4o-mini",
            purpose="plan",
            context={"messages": []},
            schema=Plan,
        )

    assert [call["schema"].__name__ for call in llm.calls] == ["Plan", "Plan"]


def test_respond_payload_retry_message_requires_answer_or_question() -> None:
    message = build_structured_retry_message(
        schema_name="_RespondPayload",
        has_prior_results=False,
        retry_nudge_style="openai_function_calling",
    )

    assert "If respond_kind='answer', you must include answer" in message
    assert "If respond_kind='clarify', you must include question" in message
    assert "Do not include mode, sub_intents, rationale" in message
    assert "Return only the structured JSON object" in message


def test_post_action_judgment_retry_message_is_explicit() -> None:
    message = build_structured_retry_message(
        schema_name="PostActionJudgment",
        has_prior_results=True,
        retry_nudge_style="openai_function_calling",
    )

    assert "outcome, reason, user_message, and optional confidence" in message
    assert "advance, retry, replan, ask_user, halt, or skip" in message
    assert "numeric value between 0.0 and 1.0" in message
    assert "Do not answer the user directly outside user_message" in message


def test_post_action_judgment_retry_message_includes_schema_summary() -> None:
    message = build_structured_retry_message(
        schema_name="PostActionJudgment",
        has_prior_results=True,
        retry_nudge_style="openai_function_calling",
        schema=PostActionJudgment,
    )

    assert "Schema: PostActionJudgment." in message
    assert "Schema keys: outcome, reason, user_message, confidence." in message
    assert "Required schema keys: outcome." in message
    assert "Schema types:" in message
    assert "Schema enums: outcome=advance|retry|replan|ask_user|halt|skip." in message


def test_closure_judgment_retry_message_is_explicit() -> None:
    message = build_structured_retry_message(
        schema_name="ClosureJudgment",
        has_prior_results=True,
        retry_nudge_style="openai_function_calling",
    )

    assert "satisfied, reason, next_action, final_answer, and optional" in message
    assert "post_completion_critique" in message
    assert "close, continue, or replan" in message
    assert "If next_action='close'" in message


def test_closure_judgment_retry_message_includes_schema_summary() -> None:
    message = build_structured_retry_message(
        schema_name="ClosureJudgment",
        has_prior_results=True,
        retry_nudge_style="openai_function_calling",
        schema=ClosureJudgment,
    )

    assert "Schema: ClosureJudgment." in message
    assert (
        "Schema keys: satisfied, reason, next_action, final_answer, "
        "post_completion_critique, plan_reconciliation, verification, "
        "review." in message
    )
    assert "Schema types:" in message
    assert "Schema enums: next_action=close|continue|replan." in message
