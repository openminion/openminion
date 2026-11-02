"""Structured native formatting must preserve caller-owned phase semantics."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openminion.modules.brain.adapters.llm import LlmctlAdapter
from openminion.modules.brain.adapters.llm.request import _build_request
from openminion.modules.brain.bootstrap.freshness_classify import (
    _build_freshness_context,
)
from openminion.modules.brain.execution.runtime.turn.post_action import (
    _post_action_hints,
)
from openminion.modules.brain.loop.strategies.research import ResearchMode
from openminion.modules.brain.loop.strategies.research.schemas import ResearchSynthesis
from openminion.modules.brain.schemas import (
    BudgetCounters,
    FreshnessContract,
    WorkingState,
)
from openminion.modules.brain.schemas import PostActionJudgment
from openminion.modules.context.render.sections import task_header
from openminion.modules.context.schemas import BuildConstraints, BuildPackRequest
from openminion.modules.llm.providers.message_payloads import _messages_openai_like
from openminion.modules.llm.providers.openai.adapter import _openai_stream_payload
from openminion.modules.llm.providers.openai.request_compatibility import (
    OpenAIRequestCompatProfile,
)
from openminion.modules.llm.schemas import (
    LLMRequest,
    LLMResponse,
    Message,
    ToolCall,
    ToolSpec,
)


def _native_payload(request):
    return _openai_stream_payload(
        request,
        model=request.model,
        request_compat=OpenAIRequestCompatProfile(),
        tool_call_strategy="native",
        tool_name_map=None,
        include_tool_contract=True,
    )


def _assert_structured_contract(payload):
    assert payload["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_output"},
    }
    assert [tool["function"]["name"] for tool in payload["tools"]] == ["submit_output"]
    instruction = payload["messages"][0]["content"]
    assert "Use only the `submit_output` tool" in instruction
    assert (
        "Do not emit XML, JSON, markdown, or prose tool envelopes for task tools."
        in instruction
    )
    assert "control phase" not in instruction
    assert "must not execute the user's task" not in instruction


@pytest.mark.parametrize("collapse", [False, True])
def test_synthesis_native_payload_preserves_evidence_and_answer_instruction(collapse):
    from openminion.modules.brain.loop.strategies.research.findings import (
        build_synthesis_prompt,
    )

    prompt = build_synthesis_prompt(
        query="Calculate the sum.", findings=[{"content": "Values: 8, 12, 20."}]
    )
    request = _build_request(
        model="fixture-model",
        purpose="summarize",
        schema=ResearchSynthesis,
        context={"messages": [{"role": "user", "content": prompt}]},
        temperature=0,
    )
    payload = _native_payload(request)
    _assert_structured_contract(payload)
    assert (
        payload["tools"][0]["function"]["parameters"]
        == ResearchSynthesis.model_json_schema()
    )
    messages = _messages_openai_like(
        request, include_fallback_instruction=False, collapse_system_messages=collapse
    )
    assert prompt in [message["content"] for message in messages]
    assert "must not execute the user's task" not in str(messages)


def test_freshness_routing_keeps_its_caller_owned_contract():
    context = _build_freshness_context(user_input="Find today's weather.")
    request = _build_request(
        model="fixture-model",
        purpose="freshness_classify",
        context=context,
        schema=FreshnessContract,
        temperature=0,
    )
    payload = _native_payload(request)
    _assert_structured_contract(payload)
    assert payload["messages"][1:] == context["messages"]
    assert "internal freshness-routing classifier" in str(payload["messages"])
    assert "Classify freshness sensitivity semantically" in str(payload["messages"])


def test_post_action_judge_keeps_its_caller_owned_contract():
    state = WorkingState(
        session_id="fixture-session",
        agent_id="fixture-agent",
        budgets_remaining=BudgetCounters(
            ticks=10, tool_calls=5, a2a_calls=2, tokens=2000, time_ms=60000
        ),
    )
    hints = _post_action_hints(
        state=state,
        current_command=None,
        llm_call_id="fixture-call",
        hint_facts={"fact_kind": "tool_result"},
    )
    header = task_header(
        BuildPackRequest(
            session_id=state.session_id,
            agent_id=state.agent_id,
            purpose="judge",
            query="Review the tool result.",
        ),
        BuildConstraints(style_overrides=hints["style_overrides"]),
    )
    request = _build_request(
        model="fixture-model",
        purpose="judge",
        schema=PostActionJudgment,
        context={"messages": [{"role": "system", "content": header}], "hints": hints},
        temperature=0,
    )
    payload = _native_payload(request)
    _assert_structured_contract(payload)
    assert payload["messages"][1]["content"] == header
    assert "post-action semantic judge" in header
    assert "advance, retry, replan, ask_user, halt, skip" in header
    assert "Tool success does not automatically mean the task is done" in header


@pytest.mark.parametrize("fallback", [False, True])
def test_ordinary_task_tools_keep_their_contract(fallback):
    request = LLMRequest(
        model="fixture-model",
        messages=[Message(role="user", content="Read evidence.")],
        tools=[
            ToolSpec(
                name="file_read",
                description="Read a file",
                input_schema={"type": "object"},
            )
        ],
        tool_choice="auto",
    )
    messages = _messages_openai_like(request, include_fallback_instruction=fallback)
    assert "submit_output" not in str(messages)
    assert ("Tool-calling contract:" in str(messages)) is fallback
    payload = _native_payload(request)
    assert payload["tool_choice"] == "auto"
    assert payload["tools"][0]["function"]["name"] == "file_read"


def test_structured_fallback_still_requires_submit_output():
    request = _build_request(
        model="fixture-model",
        purpose="summarize",
        schema=ResearchSynthesis,
        context={},
        temperature=0,
    )
    messages = _messages_openai_like(request, include_fallback_instruction=True)
    text = str(messages)
    assert "Tool-calling contract:" in text
    assert '"tool_calls"' in text
    assert "You MUST call `submit_output`. Plain text responses are not valid." in text
    assert "must not execute the user's task" not in text
    assert "Schema-only" not in text


@pytest.mark.parametrize("status", ["complete", "incomplete", "invalid"])
def test_local_synthesis_wires_native_payload_and_validates_result(status):
    captured = []
    result = {
        "answer": "The sum is 40.",
        "status": status,
        "remaining_work": "" if status == "complete" else "Verify evidence.",
    }

    def call(request):
        payload = _native_payload(request)
        _assert_structured_contract(payload)
        captured.append(payload)
        return LLMResponse(
            ok=True,
            provider="fixture",
            model=request.model,
            tool_calls=[ToolCall(name="submit_output", arguments=result)],
        )

    def build_context(**kwargs):
        return {
            "messages": [{"role": "user", "content": kwargs["hints"]["user_input"]}]
        }

    runner = SimpleNamespace(
        llm_api=LlmctlAdapter(SimpleNamespace(call=call)),
        profile=SimpleNamespace(
            llm_profiles=SimpleNamespace(summarize_model="fixture-model")
        ),
        _build_context=build_context,
        _debit_tokens=Mock(),
    )
    ctx = SimpleNamespace(
        _services=SimpleNamespace(runner=runner),
        state=WorkingState(
            session_id="fixture-session",
            agent_id="fixture-agent",
            budgets_remaining=BudgetCounters(
                ticks=10, tool_calls=5, a2a_calls=2, tokens=2000, time_ms=60000
            ),
        ),
        logger=Mock(),
    )
    synthesis = ResearchMode()._build_synthesis(
        ctx,
        query="Calculate the sum.",
        findings=[{"content": "Values: 8, 12, 20."}],
    )
    assert captured
    assert "Values: 8, 12, 20." in str(captured[0]["messages"])
    assert "Synthesize these findings into a comprehensive, coherent answer." in str(
        captured[0]["messages"]
    )
    if status == "invalid":
        assert synthesis is None
    else:
        assert synthesis.answer == "The sum is 40."
        assert synthesis.status == status
        assert synthesis.remaining_work == result["remaining_work"]
