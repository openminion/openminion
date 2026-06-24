from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from openminion.api.agent import (
    Agent,
    AgentOutputValidationError,
    AgentRunResult,
    _extract_json_object,
)


class _FakeRuntime:
    def __init__(self, reply_body: str = "hello back") -> None:
        self.reply_body = reply_body
        self.last_payload: dict[str, Any] | None = None
        self.last_progress_callback: Any = None
        self.closed = False

    def run_turn(self, *, payload, progress_callback=None, **kwargs):
        self.last_payload = payload
        self.last_progress_callback = progress_callback
        return {"body": self.reply_body, "request_id": "fake-req-1"}

    def close(self) -> None:
        self.closed = True


def test_agent_run_returns_raw_text_when_no_output_type() -> None:
    runtime = _FakeRuntime("just a string")
    agent = Agent(instructions="be brief", runtime=runtime)
    result = agent.run("hi there")
    assert isinstance(result, AgentRunResult)
    assert result.output == "just a string"
    assert result.text == "just a string"
    assert runtime.last_payload == {
        "message": "hi there",
        "system_prompt": "be brief",
    }


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


def test_agent_extracts_json_when_reply_has_prose_wrapper() -> None:
    reply = 'Sure! {"sentiment": "neutral", "summary": "test"} hope that helps.'
    runtime = _FakeRuntime(reply)
    agent = Agent(output_type=_ReplyModel, runtime=runtime)
    result = agent.run("evaluate")
    assert result.output.sentiment == "neutral"


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


def test_agent_run_stream_invokes_on_delta_callback() -> None:
    runtime = _FakeRuntime("streamed")
    captured: list[Any] = []
    agent = Agent(runtime=runtime)
    result = agent.run_stream("hello", on_delta=lambda d: captured.append(d))
    assert result.output == "streamed"
    # The progress_callback must have been threaded through (even if the fake
    # runtime never invokes it — the wiring is what we assert here).
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
    # Payload message must be the JSON-serialized model.
    body = runtime.last_payload["message"]
    assert "MCP" in body and "depth" in body


def test_agent_close_releases_owned_runtime() -> None:
    runtime = _FakeRuntime()
    # When we pass a runtime in, the agent does NOT own it — close should be
    # a no-op on the supplied runtime.
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
        agent.close()
        assert fake.closed is True


def test_extract_json_object_handles_nested_braces() -> None:
    text = 'prefix {"a": {"b": 1}} suffix'
    assert _extract_json_object(text) == '{"a": {"b": 1}}'


def test_extract_json_object_returns_none_when_no_object() -> None:
    assert _extract_json_object("no braces here") is None
    assert _extract_json_object("") is None
