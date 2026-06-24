from __future__ import annotations

import json
from typing import Any

import pytest

from openminion.modules.brain.retry import (
    STRUCTURED_FAILURE_KIND_KEY,
    STRUCTURED_HAS_TOOL_CALLS_KEY,
    STRUCTURED_RETRYABLE_KEY,
    STRUCTURED_RETRY_MESSAGE_HINT,
    call_structured_with_retry,
)
from openminion.modules.brain.schemas import (
    ClosureJudgment,
    DecisionAdapter,
    Plan,
    UserMessageCandidateReport,
)
from openminion.modules.telemetry.trace.structured import trace_context_payload
from openminion.modules.telemetry.trace.layout import resolve_trace_root


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


class _TracingFakeLLM(_FakeLLM):
    def __init__(self, responses: list[Any], *, home_root) -> None:
        super().__init__(responses)
        self._home_root = home_root

    def get_last_trace_context(self) -> dict[str, Any] | None:
        return getattr(self, "_last_trace_context", None)

    def call_structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        attempt_index = len(self.calls)
        self._last_trace_context = trace_context_payload(
            session_id="sess",
            turn_id="turn",
            inference_step=attempt_index,
            label=f"call{attempt_index:02d}",
            provider="test-provider",
            model=str(kwargs.get("model", "") or ""),
            home_root=self._home_root,
        )
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
        "question": (
            "I could not produce a valid structured decision for this request. "
            "Please rephrase or narrow the scope."
        ),
        STRUCTURED_RETRYABLE_KEY: True,
        STRUCTURED_FAILURE_KIND_KEY: "invalid_decide_tool_call",
        STRUCTURED_HAS_TOOL_CALLS_KEY: True,
    }


def test_retry_policy_retries_structured_retryable_decide_without_adapter() -> None:
    llm = _FakeLLM(
        [
            _retryable_decide_result(),
            {
                "mode": "act",
                "confidence": 0.9,
                "reason_code": "simple_weather_query",
                "act_profile": "general",
                "execution_target": {"kind": "local"},
                "rationale": "Use the shared act loop.",
            },
        ]
    )

    result = call_structured_with_retry(
        llm,
        model="test",
        purpose="decide",
        context={
            "messages": [{"role": "user", "content": "weather"}],
            "hints": {"tool_aware": True},
        },
        schema=DecisionAdapter,
    )

    assert result["route"] == "act"
    assert len(llm.calls) == 2
    retry_message = llm.calls[1]["context"]["hints"][STRUCTURED_RETRY_MESSAGE_HINT]
    assert "act_profile/execution_target" in retry_message
    assert "Execution tools are available in act phase" in retry_message


def test_retry_policy_uses_existing_result_guidance_on_replan_without_adapter() -> None:
    llm = _FakeLLM(
        [
            _retryable_decide_result(),
            {
                "mode": "respond",
                "confidence": 0.9,
                "reason_code": "existing_result_sufficient",
                "answer": "San Diego is 16C and cloudy.",
            },
        ]
    )

    result = call_structured_with_retry(
        llm,
        model="test",
        purpose="decide",
        context={
            "messages": [{"role": "user", "content": "weather"}],
            "hints": {"tool_aware": True, "has_prior_results": True},
        },
        schema=DecisionAdapter,
    )

    assert result["route"] == "respond"
    retry_message = llm.calls[1]["context"]["hints"][STRUCTURED_RETRY_MESSAGE_HINT]
    assert "Use the existing tool result already present in context" in retry_message
    assert "act_profile/execution_target" in retry_message


def test_retry_policy_fail_closed_after_second_invalid_decide_without_adapter() -> None:
    invalid = _retryable_decide_result()
    llm = _FakeLLM([invalid, dict(invalid), dict(invalid)])

    result = call_structured_with_retry(
        llm,
        model="test",
        purpose="decide",
        context={
            "messages": [{"role": "user", "content": "weather"}],
            "hints": {"tool_aware": True},
        },
        schema=DecisionAdapter,
    )

    assert result["route"] == "respond"
    assert result["reason_code"] == "invalid_decide_tool_call"
    assert "internal decision error" in result["answer"].lower()
    assert len(llm.calls) == 3


def test_retry_policy_semantic_denial_text_no_longer_retries() -> None:
    llm = _FakeLLM(
        [
            {
                "mode": "respond",
                "confidence": 1.0,
                "reason_code": "simple_factual_question",
                "answer": "I don't have access to real-time weather data.",
                STRUCTURED_HAS_TOOL_CALLS_KEY: True,
            }
        ]
    )

    result = call_structured_with_retry(
        llm,
        model="test",
        purpose="decide",
        context={
            "messages": [{"role": "user", "content": "weather"}],
            "hints": {"tool_aware": True},
        },
        schema=DecisionAdapter,
    )

    assert result["route"] == "respond"
    assert len(llm.calls) == 1


def test_retry_policy_raises_after_second_invalid_generic_output_without_adapter() -> (
    None
):
    invalid = {
        STRUCTURED_RETRYABLE_KEY: True,
        STRUCTURED_FAILURE_KIND_KEY: "invalid_structured_output",
    }
    llm = _FakeLLM([invalid, dict(invalid)])

    with pytest.raises(RuntimeError, match="structured output"):
        call_structured_with_retry(
            llm,
            model="test",
            purpose="plan",
            context={"messages": [{"role": "user", "content": "plan"}]},
            schema=Plan,
        )

    assert len(llm.calls) == 2


def test_retry_policy_does_not_retry_closure_judgment() -> None:
    invalid = {
        STRUCTURED_RETRYABLE_KEY: True,
        STRUCTURED_FAILURE_KIND_KEY: "invalid_structured_output",
    }
    llm = _FakeLLM([invalid, dict(invalid)])

    with pytest.raises(RuntimeError, match="structured output"):
        call_structured_with_retry(
            llm,
            model="test",
            purpose="judge",
            context={"messages": [{"role": "user", "content": "judge"}]},
            schema=ClosureJudgment,
        )

    assert len(llm.calls) == 1


def test_retry_policy_returns_empty_optional_user_message_candidate_report() -> None:
    invalid = {
        STRUCTURED_RETRYABLE_KEY: True,
        STRUCTURED_FAILURE_KIND_KEY: "invalid_structured_output",
    }
    llm = _FakeLLM([invalid])

    result = call_structured_with_retry(
        llm,
        model="test",
        purpose="reflect",
        context={"messages": [{"role": "user", "content": "What is my email?"}]},
        schema=UserMessageCandidateReport,
    )

    assert result == {"session_id": None, "agent_id": None, "items": []}
    assert len(llm.calls) == 1


def test_retry_policy_writes_structured_retry_sidecar(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("OPENMINION_TRACE_REQUESTS", "1")
    llm = _TracingFakeLLM(
        [
            _retryable_decide_result(),
            {
                "mode": "respond",
                "confidence": 0.9,
                "reason_code": "existing_result_sufficient",
                "respond_kind": "answer",
                "answer": "San Diego is 16C and cloudy.",
            },
        ],
        home_root=tmp_path,
    )

    result = call_structured_with_retry(
        llm,
        model="openai/gpt-5.4",
        purpose="decide",
        context={
            "messages": [{"role": "user", "content": "weather"}],
            "hints": {"tool_aware": True, "has_prior_results": True},
        },
        schema=DecisionAdapter,
    )

    assert result["route"] == "respond"
    trace_context = llm.get_last_trace_context()
    assert trace_context is not None
    trace_path = resolve_trace_root(home_root=tmp_path) / str(
        trace_context["structured_trace_filename"]
    )
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    retry = payload["retry"]
    assert retry["attempt_index"] == 1
    assert retry["schema_sequence"] == [
        "Decision",
        "SimplifiedDecision",
        "UltraSimpleDecision",
    ]
    assert retry["retry_strategy"] == "progressive_simplification"
    assert retry["outcome"] == "accepted"
    assert (
        "Use the existing tool result already present in context"
        in retry["retry_message"]
    )
