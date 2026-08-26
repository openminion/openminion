from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from openminion.api.agent import Agent
from openminion.api.handoff import (
    Handoff,
    SubagentRunContext,
    build_delegate_family_spec,
    build_delegate_tool,
    subagent,
)
from openminion.modules.tool.registry import ToolRegistry


class _FakeRuntime:
    def __init__(
        self,
        reply_body: str = "hello back",
        *,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.reply_body = reply_body
        self.tools = tools
        self.last_payload: dict[str, Any] | None = None
        self.seen_tool_names_during_run: list[str] = []
        self.prompt_visible_tools_during_run: list[str] = []
        self.tool_result_during_run: Any = None

    def run_turn(self, *, payload, progress_callback=None, **kwargs):
        self.last_payload = payload
        if self.tools is not None:
            self.seen_tool_names_during_run = sorted(self.tools.list())
            self.prompt_visible_tools_during_run = sorted(
                name
                for name, tool in self.tools.list().items()
                if bool(getattr(tool, "prompt_visible_runtime_name", False))
            )
            for name in payload.get("allowed_tools", ()):
                if name in self.tools.list():
                    self.tool_result_during_run = self.tools.get(name).handler(
                        {"message": "child marker"}, None
                    )
                    break
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
    assert result == {
        "ok": True,
        "content": "delegated reply",
        "data": {"output": "delegated reply"},
    }
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
    agent_a.run("hi")
    assert "transfer_to_agent_b" in runtime_a.last_payload["allowed_tools"]


def test_agent_handoff_tool_is_registered_only_during_run() -> None:
    parent_runtime = _FakeRuntime(tools=ToolRegistry())
    child_runtime = _FakeRuntime("target-produced marker")
    child = Agent(runtime=child_runtime, name="child")
    parent = Agent(
        runtime=parent_runtime,
        name="parent",
        handoffs=[Handoff(target=child)],
    )

    parent.run("please transfer")

    assert "transfer_to_child" in parent_runtime.seen_tool_names_during_run
    assert parent_runtime.tool_result_during_run == {
        "ok": True,
        "content": "target-produced marker",
        "data": {"output": "target-produced marker"},
    }
    assert child_runtime.last_payload["message"] == "child marker"
    assert "transfer_to_child" not in parent_runtime.tools.list()


def test_agent_handoff_tool_is_prompt_visible_during_run() -> None:
    parent_runtime = _FakeRuntime(tools=ToolRegistry())
    child = Agent(runtime=_FakeRuntime("child"), name="child")
    parent = Agent(
        runtime=parent_runtime,
        name="parent",
        handoffs=[Handoff(target=child)],
    )

    parent.run("delegate")

    registered = parent_runtime.seen_tool_names_during_run
    assert "transfer_to_child" in registered
    assert parent_runtime.prompt_visible_tools_during_run == ["transfer_to_child"]
    assert "transfer_to_child" not in parent_runtime.tools.list()


def test_agent_handoff_registration_does_not_leak_to_next_agent_run() -> None:
    runtime = _FakeRuntime(tools=ToolRegistry())
    child = Agent(runtime=_FakeRuntime("child"), name="child")
    parent = Agent(runtime=runtime, name="parent", handoffs=[Handoff(target=child)])
    unrelated = Agent(runtime=runtime, name="unrelated")

    parent.run("delegate once")
    unrelated.run("no handoff")

    assert runtime.last_payload == {"message": "no handoff"}
    assert "transfer_to_child" not in runtime.seen_tool_names_during_run
    assert "transfer_to_child" not in runtime.tools.list()


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


def test_subagent_has_explicit_bounded_run_context() -> None:
    runtime = _FakeRuntime()
    parent = Agent(runtime=runtime, name="parent")
    child = subagent(
        parent,
        name="child",
        tools=["safe.read"],
        timeout_seconds=30,
        deadline_iso="2099-07-24T12:00:00Z",
    )

    context = child.subagent_context
    assert isinstance(context, SubagentRunContext)
    assert context.parent_agent_id == "parent"
    assert context.child_agent_id == "child"
    assert context.tool_allowlist == ("safe.read",)
    assert context.timeout_seconds == 30
    assert context.memory_posture == "none"
    assert context.typed_result_handback is True
    assert context.parent_transcript_inherited is False
    assert context.hidden_reasoning_inherited is False
    assert context.implicit_memory_write is False


def test_subagent_run_threads_context_as_runtime_metadata() -> None:
    runtime = _FakeRuntime()
    parent = Agent(runtime=runtime, name="parent", instructions="parent secret")
    child = subagent(
        parent,
        name="child",
        instructions="child only",
        tools=["safe.read"],
        timeout_seconds=30,
    )

    child.run("bounded work")

    payload = runtime.last_payload
    assert payload["message"] == "bounded work"
    assert payload["system_prompt"] == "child only"
    assert "parent secret" not in str(payload)
    assert payload["allowed_tools"] == ["safe.read"]
    assert payload["timeout_seconds"] == 30
    assert payload["subagent_context"]["parent_agent_id"] == "parent"
    assert payload["subagent_context"]["child_agent_id"] == "child"
    assert payload["subagent_context"]["tool_allowlist"] == ["safe.read"]
    assert payload["subagent_context"]["implicit_memory_write"] is False
    assert payload["inbound_metadata"]["subagent_memory_posture"] == "none"
    assert payload["inbound_metadata"]["subagent_tool_allowlist"] == "safe.read"


def test_subagent_rejects_unbound_memory_posture_before_run() -> None:
    parent = Agent(runtime=_FakeRuntime(), name="parent")

    with pytest.raises(ValueError, match="requires memory_grant_id"):
        subagent(parent, memory_posture="read_only_bounded")


def test_subagent_binds_existing_read_only_memory_grant() -> None:
    parent = Agent(runtime=_FakeRuntime(), name="parent")

    child = subagent(
        parent,
        memory_posture="read_only_bounded",
        memory_grant_id="grant-1",
    )

    assert child.subagent_context is not None
    assert child.subagent_context.memory_grant_id == "grant-1"


def test_subagent_rejects_elapsed_or_unzoned_deadline() -> None:
    parent = Agent(runtime=_FakeRuntime(), name="parent")

    with pytest.raises(ValueError, match="has elapsed"):
        subagent(parent, deadline_iso="2020-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="include a timezone"):
        subagent(parent, deadline_iso="2099-01-01T00:00:00")


def test_subagent_disallowed_parent_tools_are_absent_by_default() -> None:
    runtime = _FakeRuntime()
    parent = Agent(runtime=runtime, name="parent", tools=["danger.write"])
    child = subagent(parent, name="child")

    child.run("bounded")

    payload = runtime.last_payload
    assert "allowed_tools" not in payload
    assert payload["subagent_context"]["tool_allowlist"] == []
    assert payload["inbound_metadata"]["subagent_tool_allowlist"] == ""


def test_subagent_lazy_runtime_construction_only_happens_once() -> None:
    with patch(
        "openminion.api.agent.APIRuntime.from_config_path",
    ) as factory:
        fake = _FakeRuntime()
        factory.return_value = fake
        parent = Agent()  # no runtime
        child = subagent(parent, name="child")
        assert factory.call_count == 1
        child.run("hello")
        assert factory.call_count == 1
