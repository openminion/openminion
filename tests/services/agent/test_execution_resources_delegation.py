from types import SimpleNamespace

from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool import build_default_tool_registry
from openminion.services.agent.execution.resources import ExecutionResources


def test_delegation_resources_preserve_runtime_and_approval_owners(
    monkeypatch,
) -> None:
    runtime_handle = object()

    async def approval_callback(*_args) -> bool:
        return True

    service_port = SimpleNamespace(
        config=OpenMinionConfig(),
        home_root=None,
        identity_agent_id="parent",
        logger=SimpleNamespace(getChild=lambda _name: None),
        memory_assembly=None,
    )
    runtime = SimpleNamespace(
        inbound=Message(channel="console", target="user", body="delegate"),
        approval_callback=approval_callback,
        runtime_handle=runtime_handle,
    )
    captured = {}

    def build_api(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        "openminion.services.agent.execution.resources.build_a2a_delegate_api",
        build_api,
    )

    resolved = ExecutionResources(service_port, runtime)._resolve_a2a_delegate_api()

    assert resolved is not None
    assert captured["runtime_resolver"]() is runtime_handle
    assert captured["approval_callback"] is approval_callback


def test_execution_resources_support_normal_delegate_handler(monkeypatch) -> None:
    runtime_handle = object()

    async def approval_callback(*_args) -> bool:
        return True

    class _A2AAdapter:
        def __init__(self, *, runtime_resolver, approval_callback) -> None:
            self.runtime_resolver = runtime_resolver
            self.approval_callback = approval_callback

        def call(self, *, command, session_id, trace_id):
            assert self.runtime_resolver() is runtime_handle
            assert self.approval_callback is approval_callback
            assert command["target_agent_id"] == "worker"
            assert session_id == "task-delegate::parent-session"
            assert trace_id
            return {
                "status": "success",
                "summary": "child completed",
                "outputs": {"body": "reviewed"},
            }

    def create_a2a_adapter(*_args, **kwargs):
        return _A2AAdapter(
            runtime_resolver=kwargs["runtime_resolver"],
            approval_callback=kwargs["approval_callback"],
        )

    monkeypatch.setattr(
        "openminion.modules.brain.adapters.factory.a2a.create_a2a_adapter",
        create_a2a_adapter,
    )
    config = OpenMinionConfig()
    config.runtime.memory_enabled = False
    registry = build_default_tool_registry()
    service_port = SimpleNamespace(
        config=config,
        home_root=None,
        identity_agent_id="parent",
        logger=SimpleNamespace(getChild=lambda _name: None),
        tool_selection=None,
        ops_service=None,
        tools=registry,
        telemetryctl=None,
        memory_assembly=None,
    )
    runtime = SimpleNamespace(
        inbound=Message(
            channel="console",
            target="user",
            body="delegate",
            metadata={"session_id": "parent-session"},
        ),
        approval_callback=approval_callback,
        runtime_handle=runtime_handle,
        agent_discovery_snapshot=lambda: [{"agent_id": "worker"}],
        storage_path=None,
        sandbox_runner=None,
        authored_tools=None,
    )

    context = ExecutionResources(service_port, runtime).build_context()
    batch = registry.execute_calls(
        [
            ProviderToolCall(
                id="delegate-1",
                name="task.delegate",
                arguments={
                    "agent_id": "worker",
                    "instruction": "review this",
                    "timeout_seconds": 30,
                },
            )
        ],
        context=context,
    )
    result = batch.results[0]

    assert result.ok is True
    assert result.data["agent_id"] == "worker"
    assert result.content == "child completed"
