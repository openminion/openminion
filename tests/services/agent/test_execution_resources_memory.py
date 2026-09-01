from types import SimpleNamespace

from openminion.base.config import OpenMinionConfig
from openminion.base.types import Message
from openminion.modules.policy import SecurityPolicyEngine
from openminion.modules.policy.adapters.memory import (
    memory_capture_recovery_allowed,
)
from openminion.modules.tool.registry import ToolRegistry
from openminion.services.agent.execution.resources import ExecutionResources
from openminion.services.runtime.memory import RuntimeMemoryAssembly


class _MemoryService:
    def write_record(self, **kwargs):
        return "record-1"

    def search(self, options):
        return []

    def delete_record(self, record_id, *, reason=None):
        return True


def _inputs(*, assembly=None):
    config = OpenMinionConfig()
    service_port = SimpleNamespace(
        config=config,
        home_root=None,
        identity_agent_id="alpha",
        logger=SimpleNamespace(getChild=lambda _name: None),
        tool_selection=None,
        ops_service=None,
        tools=None,
        telemetryctl=None,
        memory_assembly=assembly,
    )
    runtime = SimpleNamespace(
        inbound=Message(
            channel="console",
            target="user",
            body="remember",
            metadata={"session_id": "session-1"},
        ),
        storage_path=None,
        sandbox_runner=None,
        authored_tools=None,
        agent_discovery_snapshot=None,
        approval_callback=None,
        runtime_handle=None,
    )
    return service_port, runtime


def test_execution_resources_use_only_injected_memory_assembly() -> None:
    memory_service = _MemoryService()
    assembly = RuntimeMemoryAssembly(
        gateway=object(),
        memctl=memory_service,  # type: ignore[arg-type]
    )
    service_port, runtime = _inputs(assembly=assembly)

    context = ExecutionResources(service_port, runtime).build_context()

    assert context.memory_service is memory_service


def test_execution_resources_do_not_build_an_uninjected_memory_runtime() -> None:
    service_port, runtime = _inputs()

    context = ExecutionResources(service_port, runtime).build_context()

    assert context.memory_service is None


def test_capture_recovery_reuses_current_memory_write_policy() -> None:
    service = SimpleNamespace(
        _security_policy=SecurityPolicyEngine(),
        _tools=ToolRegistry(),
        _identity_agent_id="alpha",
    )

    assert memory_capture_recovery_allowed(
        "session-1",
        "root-1",
        "turn.outcome:1",
        "capture:1",
        policy=service._security_policy,
        tools=service._tools,
        agent_id=service._identity_agent_id,
    )

    assert not memory_capture_recovery_allowed(
        "session-1",
        "root-1",
        "turn.outcome:1",
        "capture:1",
        policy=service._security_policy,
        tools=None,
        agent_id=service._identity_agent_id,
    )
