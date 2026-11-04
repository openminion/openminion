from __future__ import annotations

import asyncio
from dataclasses import fields
from threading import Event
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
from openminion.modules.controlplane.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
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
        self.last_run_kwargs: dict[str, Any] = {}
        self.run_count = 0
        self.closed = False
        self.tool_result: Any = None

    def run_turn(self, *, payload, progress_callback=None, **kwargs):
        self.run_count += 1
        self.last_payload = payload
        self.last_progress_callback = progress_callback
        self.last_run_kwargs = dict(kwargs)
        if self.tools is not None:
            for name in payload.get("allowed_tools", ()):
                if name in self.tools.list():
                    self.tool_result = self.tools.get(name).handler(
                        {"left": 2, "right": 3}, None
                    )
                    break
        result = {
            "body": self.reply_body,
            "id": "fake-result-1",
            "metadata": {"request_id": "fake-req-1"},
            "stats": {"tokens_in": 3, "tokens_out": 2},
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
    assert result.id == "fake-result-1"
    assert result.metadata == {"request_id": "fake-req-1"}
    assert result.stats == {"tokens_in": 3, "tokens_out": 2}
    assert runtime.last_payload == {
        "message": "hi there",
        "session_id": "sdk-session",
        "deliver": False,
        "inbound_metadata": {CALLER_HANDLES_DELIVERY_METADATA_KEY: "true"},
        "override_system_prompt": "be brief",
    }


def test_agent_run_result_preserves_existing_positional_fields() -> None:
    result = AgentRunResult("output", "text", {}, "run-1", "done", "session-1")

    assert result.run_id == "run-1"
    assert result.run_state == "done"
    assert result.session_id == "session-1"
    assert result.id is None


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
    assert runtime.last_payload["inbound_metadata"] == {
        CALLER_HANDLES_DELIVERY_METADATA_KEY: "true"
    }


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


def test_agent_run_passes_existing_turn_controls_to_runtime() -> None:
    runtime = _FakeRuntime()
    cancel_event = Event()

    async def approve(_tool: str, _args: dict[str, Any], _approval_id: str) -> bool:
        return True

    Agent(runtime=runtime).run(
        "work",
        timeout_seconds=17,
        approval_callback=approve,
        cancel_event=cancel_event,
    )

    assert runtime.last_payload["timeout_seconds"] == 17
    internal_callback = runtime.last_run_kwargs["approval_callback"]
    assert internal_callback is not approve
    assert asyncio.run(internal_callback("file.write", {}, "approval-1", {})) is True
    assert runtime.last_run_kwargs["cancel_event"] is cancel_event


_RESULT_FIELDS = (
    "output",
    "text",
    "raw",
    "run_id",
    "run_state",
    "session_id",
    "id",
    "metadata",
    "stats",
)


def test_agent_run_result_keeps_its_positional_field_order() -> None:
    result = AgentRunResult(
        "output",
        "text",
        {"body": "text"},
        "run-1",
        "done",
        "session-1",
        "result-1",
        {"thread_id": "session-1:thread-1"},
        {"tokens_in": 3},
    )

    # thread_id follows the existing fields and defaults to None.
    assert tuple(item.name for item in fields(AgentRunResult)) == (
        *_RESULT_FIELDS,
        "thread_id",
    )
    assert (result.id, result.metadata, result.stats) == (
        "result-1",
        {"thread_id": "session-1:thread-1"},
        {"tokens_in": 3},
    )
    assert result.thread_id is None


@pytest.mark.parametrize("method", ["run", "run_stream"])
@pytest.mark.parametrize("thread_id", [None, "", "   "], ids=["none", "empty", "blank"])
def test_agent_run_without_a_thread_id_sends_the_same_payload(
    method: str, thread_id: str | None
) -> None:
    runtime = _FakeRuntime()
    agent = Agent(runtime=runtime, session_id="sdk-session")
    getattr(agent, method)("yes")
    default_payload = runtime.last_payload

    getattr(agent, method)("yes", thread_id=thread_id)

    assert runtime.last_payload == default_payload
    assert runtime.last_payload["inbound_metadata"] == {
        CALLER_HANDLES_DELIVERY_METADATA_KEY: "true"
    }


@pytest.mark.parametrize("method", ["run", "run_stream"])
def test_agent_run_sends_an_explicit_thread_id(method: str) -> None:
    runtime = _FakeRuntime()

    getattr(Agent(runtime=runtime), method)("yes", thread_id=" sdk-session:thread-1 ")

    assert runtime.last_payload["inbound_metadata"] == {
        CALLER_HANDLES_DELIVERY_METADATA_KEY: "true",
        "thread_id": "sdk-session:thread-1",
    }


class _MetadataRuntime(_FakeRuntime):
    """Returns the given result metadata."""

    def __init__(self, metadata: dict[str, Any]) -> None:
        super().__init__()
        self.metadata = metadata

    def run_turn(self, *, payload, progress_callback=None, **kwargs):
        result = super().run_turn(
            payload=payload, progress_callback=progress_callback, **kwargs
        )
        result["metadata"] = self.metadata
        return result


@pytest.mark.parametrize(
    ("returned", "expected"),
    [
        ({"thread_id": " sdk-session:thread-1 "}, "sdk-session:thread-1"),
        ({"thread_id": "  "}, None),
        ({}, None),
    ],
    ids=["thread", "blank", "missing"],
)
def test_agent_run_result_thread_id_comes_from_the_returned_metadata(
    returned: dict[str, str], expected: str | None
) -> None:
    metadata = {"request_id": "req-1", **returned}
    runtime = _MetadataRuntime(metadata)

    result = Agent(runtime=runtime).run("yes")

    assert result.thread_id == expected
    # The metadata is copied as returned; the runtime's dict is not changed.
    assert result.metadata == {"request_id": "req-1", **returned}
    assert result.metadata is not metadata
    assert metadata == {"request_id": "req-1", **returned}


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


def test_agent_decorated_tool_is_model_visible_during_the_run() -> None:
    """Regression for openminion#248: decorated tools must reach the model."""
    from openminion.modules.tool.schema_service import ToolSchemaService

    registry = ToolRegistry()
    seen: dict[str, Any] = {}

    class _InspectingRuntime(_FakeRuntime):
        def run_turn(self, *, payload, progress_callback=None, **kwargs):
            seen["decision"] = registry.exposure_service.decide("lookup_code")
            seen["schema_names"] = [
                item["name"]
                for item in ToolSchemaService().collect_execution_tool_schemas(
                    registry=registry
                )
            ]
            return super().run_turn(
                payload=payload, progress_callback=progress_callback, **kwargs
            )

    @tool
    def lookup_code(left: int, right: int) -> int:
        """Combine two integers into a code."""

        return left * 10 + right

    Agent(tools=[lookup_code], runtime=_InspectingRuntime(tools=registry)).run("go")

    assert seen["decision"].state == "visible"
    assert "lookup_code" in seen["schema_names"]
    assert "lookup_code" not in registry.list()


def _profile_ids(registry: ToolRegistry) -> set[str]:
    return {p.profile_id for p in registry.exposure_service.profiles}


def test_agent_releases_temporary_exposure_profiles_after_run() -> None:
    registry = ToolRegistry()
    before = _profile_ids(registry)

    @tool
    def add(left: int, right: int) -> int:
        """Add two integers."""

        return left + right

    agent = Agent(tools=[add], runtime=_FakeRuntime(tools=registry))
    for _ in range(2):  # repeated runs register and release cleanly
        agent.run("go")
        assert _profile_ids(registry) == before
        assert registry.exposure_service.decide("add").state != "visible"


def test_agent_shared_runtime_reuse_keeps_pre_existing_profiles() -> None:
    registry = ToolRegistry()

    @tool
    def add(left: int, right: int) -> int:
        """Add two integers."""

        return left + right

    (profile,) = add.tool_family_spec().exposure_profiles
    registry.exposure_service.register_profiles((profile,))
    before = _profile_ids(registry)
    runtime = _FakeRuntime(tools=registry)

    Agent(tools=[add], runtime=runtime).run("go")
    Agent(tools=[add], runtime=runtime).run("again")

    assert _profile_ids(registry) == before
    assert registry.exposure_service.profile(profile.profile_id) == profile


def test_agent_rolls_back_profiles_when_registration_fails() -> None:
    from openminion.modules.tool.errors import ToolRuntimeError

    registry = ToolRegistry()

    @tool
    def first(left: int, right: int) -> int:
        """First tool."""

        return left

    @tool(name="add")
    def clashing(left: int, right: int) -> int:
        """Clashes with a pre-registered tool."""

        return right

    @tool(name="add")
    def pre_existing(left: int, right: int) -> int:
        """Already registered."""

        return left + right

    from openminion.modules.tool.framework import derive_tool_specs

    for spec in derive_tool_specs(pre_existing.tool_family_spec()):
        registry.add(spec)
    tools_before = set(registry.list())
    profiles_before = _profile_ids(registry)
    runtime = _FakeRuntime(tools=registry)

    with pytest.raises((ToolRuntimeError, ValueError)):
        Agent(tools=[first, clashing], runtime=runtime).run("go")

    assert runtime.run_count == 0
    assert set(registry.list()) == tools_before
    assert _profile_ids(registry) == profiles_before


def test_agent_rejects_temporary_tools_without_runtime_registry() -> None:
    runtime = _FakeRuntime()

    @tool
    def add(left: int, right: int) -> int:
        """Add two integers."""

        return left + right

    with pytest.raises(TypeError, match="require APIRuntime.tools"):
        Agent(tools=[add], runtime=runtime).run("add")

    assert runtime.run_count == 0


def test_agent_removes_temporary_tools_when_runtime_fails() -> None:
    registry = ToolRegistry()

    class _FailingRuntime(_FakeRuntime):
        def run_turn(self, *, payload, progress_callback=None, **kwargs):
            del payload, progress_callback, kwargs
            raise RuntimeError("provider failed")

    @tool
    def add(left: int, right: int) -> int:
        """Add two integers."""

        return left + right

    with pytest.raises(RuntimeError, match="provider failed"):
        Agent(tools=[add], runtime=_FailingRuntime(tools=registry)).run("add")

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
    captured: list[object] = []
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


@pytest.mark.parametrize("raw", [None, "reply", {"text": "alias"}, {"reply": "alias"}])
def test_agent_rejects_noncanonical_runtime_result(raw: object) -> None:
    class _MalformedRuntime(_FakeRuntime):
        def run_turn(self, *, payload, progress_callback=None, **kwargs):
            del payload, progress_callback, kwargs
            return raw

    with pytest.raises(RuntimeError, match="no canonical body"):
        Agent(runtime=_MalformedRuntime()).run("hello")


def test_agent_rejects_invalid_result_facts_before_output_validation() -> None:
    class _MalformedRuntime(_FakeRuntime):
        def run_turn(self, *, payload, progress_callback=None, **kwargs):
            del payload, progress_callback, kwargs
            return {"body": "not json", "metadata": [], "stats": {}}

    with pytest.raises(RuntimeError, match="invalid result facts"):
        Agent(output_type=_ReplyModel, runtime=_MalformedRuntime()).run("hello")


def test_extract_json_object_handles_nested_braces() -> None:
    text = 'prefix {"a": {"b": 1}} suffix'
    assert _extract_json_object(text) == '{"a": {"b": 1}}'


def test_extract_json_object_returns_none_when_no_object() -> None:
    assert _extract_json_object("no braces here") is None
    assert _extract_json_object("") is None
