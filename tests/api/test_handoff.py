from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from typing import Any
from unittest.mock import patch

import pytest

from openminion.api.agent import Agent
from openminion.api.handoff import (
    DelegatedMemoryReadRequest,
    Handoff,
    SubagentRunContext,
    build_delegate_family_spec,
    build_delegate_tool,
    subagent,
)
from openminion.modules.policy.models import PolicyConfig
from openminion.modules.policy.runtime.service import PolicyCtl
from openminion.modules.tool.registry import ToolRegistry
from sophiagraph.models import MemoryNamespace


class _FakeRuntime:
    def __init__(
        self,
        reply_body: str = "hello back",
        *,
        tools: ToolRegistry | None = None,
        action_policy: PolicyCtl | None = None,
    ) -> None:
        self.reply_body = reply_body
        self.tools = tools
        self.action_policy = action_policy
        self.last_payload: dict[str, Any] | None = None
        self.last_trusted_subagent_context: SubagentRunContext | None = None
        self.active_grant_during_run = None
        self.seen_tool_names_during_run: list[str] = []
        self.prompt_visible_tools_during_run: list[str] = []
        self.tool_result_during_run: Any = None

    def run_turn(self, *, payload, progress_callback=None, **kwargs):
        self.last_payload = payload
        self.last_trusted_subagent_context = kwargs.get("trusted_subagent_context")
        context = self.last_trusted_subagent_context
        if (
            context is not None
            and context.memory_grant_id is not None
            and self.action_policy is not None
        ):
            self.active_grant_during_run = (
                self.action_policy.resolve_active_grant_for_use(
                    context.memory_grant_id,
                    subject_id=context.child_agent_id,
                    tool="memory",
                    method="delegated_read",
                )
            )
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
    runtime_a = _FakeRuntime(tools=ToolRegistry())
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

    assert runtime.last_payload["message"] == "no handoff"
    assert runtime.last_payload["deliver"] is False
    assert runtime.last_payload["session_id"] == unrelated.session_id
    assert "transfer_to_child" not in runtime.seen_tool_names_during_run
    assert "transfer_to_child" not in runtime.tools.list()


def test_concurrent_handoff_runs_do_not_collide() -> None:
    class _BlockingRuntime(_FakeRuntime):
        def __init__(self) -> None:
            super().__init__(tools=ToolRegistry())
            self.first_entered = Event()
            self.release_first = Event()
            self._call_lock = Lock()
            self._call_count = 0

        def run_turn(self, *, payload, progress_callback=None, **kwargs):
            with self._call_lock:
                self._call_count += 1
                call_number = self._call_count
            if call_number == 1:
                self.first_entered.set()
                assert self.release_first.wait(2.0)
            return super().run_turn(
                payload=payload, progress_callback=progress_callback, **kwargs
            )

    runtime = _BlockingRuntime()
    child = Agent(runtime=_FakeRuntime("child"), name="child")
    first_parent = Agent(
        runtime=runtime,
        name="first-parent",
        handoffs=[Handoff(target=child)],
    )
    second_parent = Agent(
        runtime=runtime,
        name="second-parent",
        handoffs=[Handoff(target=child)],
    )
    second_started = Event()

    def run_second() -> Any:
        second_started.set()
        return second_parent.run("second")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(first_parent.run, "first")
        assert runtime.first_entered.wait(1.0)
        second = executor.submit(run_second)
        assert second_started.wait(1.0)
        assert not second.done()
        runtime.release_first.set()
        assert first.result(timeout=1.0).text == "hello back"
        assert second.result(timeout=1.0).text == "hello back"

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
    assert payload["override_system_prompt"] == "child only"
    assert "parent secret" not in str(payload)
    assert payload["allowed_tools"] == ["safe.read"]
    assert payload["timeout_seconds"] == 30
    assert "subagent_context" not in payload
    assert "inbound_metadata" not in payload
    context = runtime.last_trusted_subagent_context
    assert context is not None
    assert context.parent_agent_id == "parent"
    assert context.child_agent_id == "child"
    assert context.tool_allowlist == ("safe.read",)
    assert context.implicit_memory_write is False
    assert context.memory_posture == "none"


def test_subagent_rejects_unbound_memory_posture_before_run() -> None:
    parent = Agent(runtime=_FakeRuntime(), name="parent")

    with pytest.raises(ValueError, match="memory grants are not bound"):
        subagent(parent, memory_posture="read_only_bounded")


def test_subagent_rejects_memory_grant_until_runtime_binding_exists() -> None:
    parent = Agent(runtime=_FakeRuntime(), name="parent")

    with pytest.raises(ValueError, match="memory grants are not bound"):
        subagent(
            parent,
            memory_posture="read_only_bounded",
            memory_grant_id="grant-1",
        )


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
    assert runtime.last_trusted_subagent_context is not None
    assert runtime.last_trusted_subagent_context.tool_allowlist == ()


def test_subagent_issues_and_revokes_fresh_memory_grant_per_run(tmp_path) -> None:
    policy = PolicyCtl.with_sqlite(
        tmp_path / "policy.db",
        config=PolicyConfig(mode="enforce"),
    )
    try:
        runtime = _FakeRuntime(action_policy=policy)
        parent = Agent(runtime=runtime, name="parent")
        child = subagent(
            parent,
            name="child",
            timeout_seconds=30,
            memory=DelegatedMemoryReadRequest(
                namespaces=(
                    MemoryNamespace(
                        agent_id="parent",
                        project_id="project",
                        graph_id="main",
                    ),
                ),
                workspace_ids=("workspace",),
                record_types=("fact",),
                max_results=3,
                max_context_tokens=256,
            ),
        )

        child.run("first")
        first_context = runtime.last_trusted_subagent_context
        assert first_context is not None
        assert runtime.active_grant_during_run is not None
        assert first_context.memory_posture == "read_only_bounded"
        assert policy.list_grants()[0].revoked_at is not None

        runtime.active_grant_during_run = None
        child.run("second")
        second_context = runtime.last_trusted_subagent_context
        assert second_context is not None
        assert second_context.context_id != first_context.context_id
        assert second_context.child_run_id != first_context.child_run_id
        assert second_context.memory_grant_id != first_context.memory_grant_id
        assert runtime.active_grant_during_run is not None
        assert all(grant.revoked_at is not None for grant in policy.list_grants())
    finally:
        policy.close()


def test_delegated_memory_is_not_granted_before_tool_registry_validation(
    tmp_path,
) -> None:
    policy = PolicyCtl.with_sqlite(
        tmp_path / "policy.db",
        config=PolicyConfig(mode="enforce"),
    )
    try:
        runtime = _FakeRuntime(action_policy=policy)
        parent = Agent(runtime=runtime, name="parent")
        request = DelegatedMemoryReadRequest(
            namespaces=(MemoryNamespace(agent_id="parent"),),
            record_types=("fact",),
        )
        child = subagent(parent, name="child", memory=request)
        child_with_handoff = Agent(
            runtime=runtime,
            name="child",
            handoffs=[Handoff(target=Agent(runtime=runtime, name="peer"))],
            subagent_context=child.subagent_context,
            delegated_memory_request=request,
        )

        with pytest.raises(TypeError, match="require APIRuntime.tools"):
            child_with_handoff.run("delegate")

        assert policy.list_grants() == []
    finally:
        policy.close()


def test_delegated_memory_ancestry_cannot_be_laundered_through_child() -> None:
    root = Agent(runtime=_FakeRuntime(), name="root")
    memory_child = subagent(
        root,
        name="memory-child",
        memory=DelegatedMemoryReadRequest(
            namespaces=(MemoryNamespace(agent_id="root"),),
            record_types=("fact",),
        ),
    )
    middle = subagent(memory_child, name="middle")

    assert middle.subagent_context is not None
    assert middle.subagent_context.delegated_memory_ancestor is True
    with pytest.raises(ValueError, match="cannot be re-shared"):
        subagent(
            middle,
            name="leaf",
            memory=DelegatedMemoryReadRequest(
                namespaces=(MemoryNamespace(agent_id="root"),),
                record_types=("fact",),
            ),
        )


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
