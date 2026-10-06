from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from openminion import tool
from openminion.api.agent import (
    Agent,
    AgentOutputValidationError,
    AgentRunResult,
    _extract_json_object,
)
from openminion.modules.tool.registry import ToolRegistry


class _FakeRuntime:
    def __init__(
        self,
        reply_body: str = "hello back",
        *,
        run_id: str | None = None,
        run_state: str | None = None,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.reply_body = reply_body
        self.run_id = run_id
        self.run_state = run_state
        self.tools = tools
        self.last_payload: dict[str, Any] | None = None
        self.last_progress_callback: Any = None
        self.closed = False
        self.tool_result: Any = None

    def run_turn(self, *, payload, progress_callback=None, **kwargs):
        self.last_payload = payload
        self.last_progress_callback = progress_callback
        if self.tools is not None:
            for name in payload.get("allowed_tools", ()):
                if name in self.tools.list():
                    self.tool_result = self.tools.get(name).handler(
                        {"left": 2, "right": 3}, None
                    )
                    break
        result = {
            "body": self.reply_body,
            "request_id": "fake-req-1",
            "session_id": payload.get("session_id"),
        }
        if self.run_id:
            result["run_id"] = self.run_id
        if self.run_state:
            result["run_state"] = self.run_state
        return result

    def close(self) -> None:
        self.closed = True


def test_agent_run_returns_raw_text_when_no_output_type() -> None:
    runtime = _FakeRuntime("just a string")
    agent = Agent(
        instructions="be brief",
        runtime=runtime,
        session_id="sdk-session",
    )
    result = agent.run("hi there")
    assert isinstance(result, AgentRunResult)
    assert result.output == "just a string"
    assert result.text == "just a string"
    assert result.session_id == "sdk-session"
    assert runtime.last_payload == {
        "message": "hi there",
        "session_id": "sdk-session",
        "deliver": False,
        "override_system_prompt": "be brief",
    }


def test_agent_owns_an_isolated_default_session() -> None:
    first = Agent(runtime=_FakeRuntime())
    second = Agent(runtime=_FakeRuntime())

    assert first.session_id
    assert second.session_id
    assert first.session_id != second.session_id


def test_agent_run_accepts_a_session_override() -> None:
    runtime = _FakeRuntime()
    agent = Agent(runtime=runtime, session_id="default-session")

    agent.run("hello", session_id="request-session")

    assert runtime.last_payload["session_id"] == "request-session"
    assert runtime.last_payload["deliver"] is False


def test_agent_profile_id_propagates_to_payload() -> None:
    runtime = _FakeRuntime()

    Agent(runtime=runtime, agent_id="reviewer").run("hello")

    assert runtime.last_payload["agent_id"] == "reviewer"


def test_agent_uses_explicit_config_path() -> None:
    with patch("openminion.api.agent.APIRuntime.from_config_path") as factory:
        fake = _FakeRuntime()
        factory.return_value = fake

        Agent(config_path="project-agents.json").run("hello")

        factory.assert_called_once_with(
            "project-agents.json", logging_mode="interactive"
        )


def test_agent_rejects_runtime_and_config_path_together() -> None:
    with pytest.raises(ValueError, match="runtime and config_path"):
        Agent(runtime=_FakeRuntime(), config_path="agents.json")


class _ReplyModel(BaseModel):
    sentiment: str
    summary: str


def test_agent_run_validates_pydantic_output_type() -> None:
    runtime = _FakeRuntime('{"sentiment": "positive", "summary": "ok"}')
    agent = Agent(output_type=_ReplyModel, runtime=runtime)
    result = agent.run("evaluate")
    assert isinstance(result.output, _ReplyModel)
    assert result.output.sentiment == "positive"
    assert result.output.summary == "ok"


def test_agent_run_preserves_available_run_identity() -> None:
    runtime = _FakeRuntime("done", run_id="run-1", run_state="completed")

    result = Agent(runtime=runtime).run("work")

    assert result.run_id == "run-1"
    assert result.run_state == "completed"


def test_agent_extracts_json_when_reply_has_prose_wrapper() -> None:
    reply = 'Sure! {"sentiment": "neutral", "summary": "test"} hope that helps.'
    runtime = _FakeRuntime(reply)
    agent = Agent(output_type=_ReplyModel, runtime=runtime)
    result = agent.run("evaluate")
    assert result.output.sentiment == "neutral"


def test_agent_extracts_json_when_string_contains_a_closing_brace() -> None:
    reply = '{"sentiment": "neutral", "summary": "keep } as text"}'
    result = Agent(output_type=_ReplyModel, runtime=_FakeRuntime(reply)).run("evaluate")

    assert result.output.summary == "keep } as text"


def test_agent_raises_validation_error_on_unparseable_reply() -> None:
    runtime = _FakeRuntime("not json at all")
    agent = Agent(output_type=_ReplyModel, runtime=runtime)
    with pytest.raises(AgentOutputValidationError) as exc_info:
        agent.run("evaluate")
    assert exc_info.value.raw_text == "not json at all"
    assert exc_info.value.validation_error is not None


def test_agent_model_param_propagates_to_payload() -> None:
    runtime = _FakeRuntime()
    agent = Agent(model="anthropic:claude-opus-4-7", runtime=runtime)
    agent.run("hello")
    assert runtime.last_payload["override_model"] == "anthropic:claude-opus-4-7"


def test_agent_tools_param_propagates_to_payload() -> None:
    runtime = _FakeRuntime()
    agent = Agent(tools=["search", "fetch"], runtime=runtime)
    agent.run("hello")
    assert runtime.last_payload["allowed_tools"] == ["search", "fetch"]


def test_agent_registers_decorated_tool_only_for_the_run() -> None:
    registry = ToolRegistry()
    runtime = _FakeRuntime(tools=registry)

    @tool
    def add(left: int, right: int) -> int:
        """Add two integers."""

        return left + right

    agent = Agent(tools=[add], runtime=runtime)
    agent.run("add two numbers")

    assert runtime.last_payload["allowed_tools"] == ["add"]
    assert runtime.tool_result == 5
    assert "add" not in registry.list()


def test_agent_rejects_undecorated_callable_tool() -> None:
    def add(left: int, right: int) -> int:
        return left + right

    with pytest.raises(TypeError, match="openminion.tool"):
        Agent(tools=[add], runtime=_FakeRuntime())


def test_agent_forced_tools_param_propagates_to_runtime_owner() -> None:
    runtime = _FakeRuntime()
    agent = Agent(forced_tools=["search"], runtime=runtime)
    agent.run("hello")
    assert runtime.last_payload["forced_tools"] == ["search"]


def test_agent_run_stream_invokes_on_delta_callback() -> None:
    runtime = _FakeRuntime("streamed")
    captured: list[Any] = []
    agent = Agent(runtime=runtime)
    result = agent.run_stream("hello", on_delta=lambda d: captured.append(d))
    assert result.output == "streamed"
    assert runtime.last_progress_callback is not None


def test_agent_run_stream_without_callback_is_noop_equivalent() -> None:
    runtime = _FakeRuntime("ok")
    agent = Agent(runtime=runtime)
    result = agent.run_stream("hello")
    assert result.text == "ok"


def test_agent_serializes_pydantic_input() -> None:
    runtime = _FakeRuntime()

    class _Input(BaseModel):
        topic: str
        depth: int

    agent = Agent(runtime=runtime)
    agent.run(_Input(topic="MCP", depth=2))
    body = runtime.last_payload["message"]
    assert "MCP" in body and "depth" in body


def test_agent_close_releases_owned_runtime() -> None:
    runtime = _FakeRuntime()
    agent = Agent(runtime=runtime)
    agent.close()
    assert runtime.closed is False


def test_agent_close_releases_default_runtime_when_constructed() -> None:
    with patch(
        "openminion.api.agent.APIRuntime.from_config_path",
    ) as factory:
        fake = _FakeRuntime()
        factory.return_value = fake
        agent = Agent()  # no runtime supplied; agent will construct one lazily
        agent.run("hi")
        factory.assert_called_once_with(None, logging_mode="interactive")
        agent.close()
        assert fake.closed is True


def test_agent_context_manager_closes_owned_runtime() -> None:
    with patch("openminion.api.agent.APIRuntime.from_config_path") as factory:
        fake = _FakeRuntime()
        factory.return_value = fake

        with Agent() as agent:
            agent.run("hello")

        assert fake.closed is True


def test_extract_json_object_handles_nested_braces() -> None:
    text = 'prefix {"a": {"b": 1}} suffix'
    assert _extract_json_object(text) == '{"a": {"b": 1}}'


def test_extract_json_object_returns_none_when_no_object() -> None:
    assert _extract_json_object("no braces here") is None
    assert _extract_json_object("") is None
