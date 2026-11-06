"""Affordable entry requests trim optional context before their final budget guard."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openminion.modules.brain.loop.context.task_context import (
    message_token_estimate,
    updated_task_context,
)
from openminion.modules.brain.loop.entry import clarify_tool_spec
from openminion.modules.llm.schemas import Message, ToolSpec
from tests.brain.test_unified_turn_entry import _build_runner, _state, _tool_response


class Provider:
    def __init__(self, *, active):
        self.active = active
        self.requests = []

    def estimate_tokens(self, *, model, context):
        return message_token_estimate(
            SimpleNamespace(),
            [Message.model_validate(item) for item in context.get("messages", [])],
            tools=[ToolSpec.model_validate(item) for item in context.get("tools", [])],
        )

    def call(self, request):
        self.requests.append(request)
        arguments = {"path": "report.json"}
        if self.active:
            arguments["task_update"] = {
                "operation": "update",
                "expected_revision": 1,
                "user_quote": "Read report.json",
                "set_conditions": [{"key": "filename", "quote": "report.json"}],
            }
        return _tool_response("file.read", arguments)


def context_with_optional_descriptions(**kwargs):
    hints = kwargs["hints"]
    return {
        "messages": [
            Message(
                role="system",
                content=json.dumps({"modes": hints["decision_route_descriptions"]}),
            ),
            Message(role="user", content=hints["user_input"]),
        ]
    }


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("oversized", [False, True])
def test_entry_trims_and_dispatches_with_4000_tokens_or_rejects_required_input(
    tmp_path, monkeypatch, active, oversized
):
    from openminion.modules.brain.loop import orchestration

    provider = Provider(active=active)
    runner = _build_runner(tmp_path, llm_api=provider)
    runner.profile.budgets.max_total_llm_tokens = 4000
    state = _state()
    state.budgets_remaining.tokens = 4000
    if active:
        state.resolved_task_context = updated_task_context(
            None,
            {
                "operation": "start",
                "expected_revision": 0,
                "user_quote": "Read JSON",
                "objective": "Read the report",
                "set_conditions": [{"key": "format", "quote": "JSON"}],
            },
            user_input="Read JSON",
        )
    before = state.model_dump()
    monkeypatch.setattr(runner, "_build_context", context_with_optional_descriptions)
    monkeypatch.setattr(
        orchestration,
        "decision_route_descriptions",
        lambda profile: {"general": "x" * 20000},
    )
    tools = [
        clarify_tool_spec(),
        ToolSpec(
            name="file.read",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        ),
    ]
    monkeypatch.setattr(
        orchestration, "build_entry_tool_specs", lambda *a, **k: (tools, True)
    )
    logger = Mock()
    user_input = "Read report.json" + ("x" * 20000 if oversized else "")
    decision = runner._decide(
        state=state, user_input=user_input, logger=logger, forced_tools=["file.read"]
    )
    strategies = [
        call.args[1]["strategy"]
        for call in logger.emit.call_args_list
        if call.args[0] == "llm.context.trimmed"
    ]
    assert "decision_route_descriptions_compacted" in strategies
    if oversized:
        assert decision.reason_code == "token_budget_exceeded"
        assert provider.requests == [] and state.llm_calls_used == 0
        assert state.budgets_remaining.tokens == 4000
        assert (
            state.resolved_task_context.model_dump() == before["resolved_task_context"]
            if active
            else state.resolved_task_context is None
        )
        return
    assert decision.route == "act" and len(provider.requests) == 1, (
        decision,
        len(provider.requests),
    )
    request = provider.requests[0]
    actual = message_token_estimate(
        SimpleNamespace(), request.messages, tools=request.tools
    )
    assert actual < 4000 and actual + request.max_output_tokens <= 4000
    assert all("x" * 100 not in message.content for message in request.messages)
    assert decision._entry_response.tool_calls[0].arguments == {"path": "report.json"}
    if active:
        assert state.resolved_task_context.conditions == {
            "format": "JSON",
            "filename": "report.json",
        }
