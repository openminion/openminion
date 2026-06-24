from __future__ import annotations

from typing import Any
from unittest.mock import patch

from openminion.api.agent import Agent
from openminion.api.handoff import (
    Handoff,
    build_delegate_family_spec,
    build_delegate_tool,
    subagent,
)


class _FakeRuntime:
    def __init__(self, reply_body: str = "hello back") -> None:
        self.reply_body = reply_body
        self.last_payload: dict[str, Any] | None = None

    def run_turn(self, *, payload, progress_callback=None, **kwargs):
        self.last_payload = payload
        return {"body": self.reply_body, "request_id": "fake"}

    def close(self) -> None:
        pass


def test_build_delegate_tool_uses_transfer_to_naming() -> None:
    runtime = _FakeRuntime("from B")
    target = Agent(runtime=runtime, name="refund_agent", instructions="Handle refunds.")
    handoff = Handoff(target=target)
    decl = build_delegate_tool(handoff)
    assert decl.name == "transfer_to_refund_agent"
    assert "Handle refunds" in decl.description
    assert "handoff" in decl.tags


def test_build_delegate_tool_runs_target_agent() -> None:
    runtime = _FakeRuntime("delegated reply")
    target = Agent(runtime=runtime, name="refund_agent")
    handoff = Handoff(target=target)
    decl = build_delegate_tool(handoff)
    args = decl.args_model(message="please refund")
    result = decl.handler(args)
    assert result == "delegated reply"
    assert runtime.last_payload["message"] == "please refund"


def test_handoff_description_falls_back_to_target_instructions_first_line() -> None:
    runtime = _FakeRuntime()
    target = Agent(
        runtime=runtime,
        name="t",
        instructions="First line.\nSecond line which should not appear.",
    )
    decl = build_delegate_tool(Handoff(target=target))
    assert decl.description == "First line."


def test_handoff_explicit_name_and_description_override() -> None:
    runtime = _FakeRuntime()
    target = Agent(runtime=runtime, name="t", instructions="anything")
    handoff = Handoff(
        target=target,
        name="custom_handoff",
        description="explicit description here",
    )
    decl = build_delegate_tool(handoff)
    assert decl.name == "custom_handoff"
    assert decl.description == "explicit description here"


def test_agent_handoffs_param_registers_handoff_tool_names() -> None:
    runtime_a = _FakeRuntime()
    runtime_b = _FakeRuntime("from B")
    agent_b = Agent(runtime=runtime_b, name="agent_b", instructions="B's job")
    agent_a = Agent(
        runtime=runtime_a,
        name="agent_a",
        handoffs=[Handoff(target=agent_b)],
    )
    assert agent_a.handoff_tool_names == ["transfer_to_agent_b"]
    # The handoff tool name is appended to the allowed-tools list passed to
    # the runtime payload.
    agent_a.run("hi")
    assert "transfer_to_agent_b" in runtime_a.last_payload["allowed_tools"]


def test_agent_handoffs_param_compiles_to_family_spec() -> None:
    runtime = _FakeRuntime()
    agent_b = Agent(runtime=runtime, name="b")
    agent_c = Agent(runtime=runtime, name="c")
    spec = build_delegate_family_spec(
        [Handoff(target=agent_b), Handoff(target=agent_c)]
    )
    assert spec is not None
    assert spec.module_id == "openminion.api.handoff.delegate"
    assert len(spec.tools) == 2
    assert {t.name for t in spec.tools} == {
        "transfer_to_b",
        "transfer_to_c",
    }


def test_build_delegate_family_spec_returns_none_when_no_handoffs() -> None:
    assert build_delegate_family_spec([]) is None


def test_subagent_reuses_parent_runtime() -> None:
    runtime = _FakeRuntime()
    parent = Agent(runtime=runtime, name="parent")
    child = subagent(parent, instructions="child task")
    assert child._runtime is runtime
    assert child._owns_runtime is False
    child.close()


def test_subagent_propagates_name_and_model() -> None:
    runtime = _FakeRuntime()
    parent = Agent(runtime=runtime, name="parent")
    child = subagent(parent, model="anthropic:claude-haiku", name="haiku-helper")
    assert child.model == "anthropic:claude-haiku"
    assert child.name == "haiku-helper"


def test_subagent_lazy_runtime_construction_only_happens_once() -> None:

    with patch(
        "openminion.api.agent.APIRuntime.from_config_path",
    ) as factory:
        fake = _FakeRuntime()
        factory.return_value = fake
        parent = Agent()  # no runtime
        child = subagent(parent, name="child")
        # The factory is invoked once, when the subagent helper called
        # parent._ensure_runtime().
        assert factory.call_count == 1
        # Now the child shares the parent's runtime; running the child does
        # NOT call the factory again.
        child.run("hello")
        assert factory.call_count == 1
