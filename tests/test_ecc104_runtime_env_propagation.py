from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import patch

import openminion.api.operations.tools as api_tools_operations
import openminion.api.routes.tools as api_tools_routes
import openminion.cli.commands.tools as cli_tools_commands
from openminion.api.routes.contracts import APIRouteContext
from openminion.base.config import OpenMinionConfig
from openminion.base.config.runtime.tools import ToolRuntimeConfig
from openminion.base.types import Message
from openminion.modules.brain.adapters.tool import ToolAdapter
from openminion.modules.llm.providers.base import ProviderToolCall, ProviderToolSpec
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.tool.runtime.policy import Policy
from openminion.modules.tool.registry import ToolExecutionBatch, ToolRegistry
from openminion.modules.session.storage.repository import create_sqlite_cron_repository
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.modules.brain.paths import resolve_brain_sessions_db_path
from openminion.modules.task import TaskManager
from openminion.services.agent.execution.fallbacks import AgentToolFallbacksMixin
from openminion.services.agent.execution.composition import build_service_port
from openminion.services.agent.execution.runtime import ExecutorRuntime
from tests._csc_fixtures import _csc_install_default_agent


def test_turn_executor_injects_runtime_env_into_tool_execution_context():
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.env = {"ECC_104_EXECUTOR": "enabled"}
    config.runtime.tools = ToolRuntimeConfig(
        search={
            "enabled_providers": ["brave", "tavily"],
            "default_provider": "brave",
            "provider_order": ["brave", "tavily"],
            "allow_fallback": False,
        }
    )
    captured: dict[str, object] = {}

    class _Tools:
        def execute_calls(self, calls, context):
            captured["context"] = context
            return ToolExecutionBatch(results=[])

    class _Selection:
        @staticmethod
        def runtime_binding_policy_metadata():
            return {
                "tool_binding_mode": "ranked",
                "tool_binding_source": "runtime_policy",
            }

    inbound = Message(channel="console", target="user", body="hello", metadata={})
    service = SimpleNamespace(
        _config=config,
        _identity_agent_id="agent-1",
        _tool_selection=_Selection(),
        _tools=_Tools(),
        _security_policy=None,
    )
    runtime = SimpleNamespace(inbound=inbound)
    runtime_ops = ExecutorRuntime(
        service_port=build_service_port(service), runtime=runtime
    )

    built = runtime_ops._build_tool_execution_context()
    assert built.metadata["runtime_env"] == {"ECC_104_EXECUTOR": "enabled"}
    assert built.metadata["runtime_tools"]["search"]["default_provider"] == "brave"
    assert built.metadata["runtime_tools"]["search"]["allow_fallback"] is False
    assert built.metadata["tool_binding_mode"] == "ranked"
    assert built.metadata["tool_binding_source"] == "runtime_policy"

    asyncio.run(
        runtime_ops.execute_tool_calls(
            [ProviderToolCall(name="weather", arguments={}, source="test")],
            tool_budget_state=None,
            context_metadata_overrides={"runtime_env": {"ECC_104_OVERRIDE": "1"}},
        )
    )
    override_context = captured["context"]
    assert override_context.metadata["runtime_env"] == {"ECC_104_OVERRIDE": "1"}
    assert override_context.metadata["runtime_tools"]["search"]["provider_order"] == [
        "brave",
        "tavily",
    ]
    assert override_context.metadata["tool_binding_mode"] == "ranked"
    assert override_context.metadata["agent_id"] == "agent-1"


def test_turn_executor_injects_resolved_runtime_storage_path():
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.storage.path = "state/openminion.db"
    config.runtime.env = {"OPENMINION_DATA_ROOT": "/tmp/ecc104-data-root"}

    service = SimpleNamespace(
        _config=config,
        _identity_agent_id="agent-1",
        _tool_selection=None,
        _tools=None,
        _security_policy=None,
    )
    resolved_storage_path = resolve_database_path(
        config.storage.path,
        env=config.runtime.env,
    )
    runtime = SimpleNamespace(
        inbound=Message(channel="console", target="user", body="hello", metadata={}),
        storage_path=resolved_storage_path,
    )

    runtime_ops = ExecutorRuntime(
        service_port=build_service_port(service), runtime=runtime
    )
    built = runtime_ops._build_tool_execution_context()

    assert built.metadata["storage_path"] == str(resolved_storage_path)
    assert built.metadata["storage_path"] != config.storage.path


def test_turn_executor_injects_memory_service_into_tool_execution_context(
    tmp_path,
):
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.storage.path = "state/openminion.db"
    config.runtime.memory_enabled = True
    config.runtime.memory_provider = "memory_v2"
    config.runtime.env = {
        "OPENMINION_HOME": str(tmp_path),
        "OPENMINION_DATA_ROOT": str(tmp_path / ".openminion"),
    }

    service = SimpleNamespace(
        _config=config,
        _identity_agent_id="agent-1",
        _tool_selection=None,
        _tools=None,
        _security_policy=None,
        _logger=logging.getLogger("tests.executor.memory"),
        _home_root=tmp_path,
    )
    runtime = SimpleNamespace(
        inbound=Message(channel="console", target="user", body="hello", metadata={})
    )
    runtime_ops = ExecutorRuntime(
        service_port=build_service_port(service), runtime=runtime
    )

    built = runtime_ops._build_tool_execution_context()

    assert built.memory_service is not None
    assert callable(getattr(built.memory_service, "write_record", None))
    assert built.metadata["memory_enabled"] == "true"
    assert built.metadata["memory_provider"] == "memory_v2"


def test_turn_executor_injects_sandbox_runner_into_tool_execution_context():
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]

    service = SimpleNamespace(
        _config=config,
        _identity_agent_id="agent-1",
        _tool_selection=None,
        _tools=None,
        _security_policy=None,
    )
    sentinel_runner = object()
    runtime = SimpleNamespace(
        inbound=Message(channel="console", target="user", body="hello", metadata={}),
        sandbox_runner=sentinel_runner,
    )
    runtime_ops = ExecutorRuntime(
        service_port=build_service_port(service), runtime=runtime
    )

    built = runtime_ops._build_tool_execution_context()

    assert built.sandbox_runner is sentinel_runner


def test_api_tools_run_route_injects_runtime_env(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeSessions:
        @staticmethod
        def resolve_session(*, agent_id, channel, target, session_id):
            del agent_id, channel, target, session_id
            return SimpleNamespace(id="session-api")

        @staticmethod
        def append_event(*, session_id, event_type, payload):
            del session_id, event_type, payload

    class _FakeTools:
        def execute_calls(self, calls, context):
            del calls
            captured["context"] = context
            return SimpleNamespace(
                results=[
                    ToolExecutionResult(
                        tool_name="weather",
                        ok=True,
                        content="ok",
                    )
                ]
            )

    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.env = {"ECC_104_API": "enabled"}
    fake_runtime = SimpleNamespace(
        config=config,
        tools=_FakeTools(),
        sessions=_FakeSessions(),
        close=lambda: None,
    )

    monkeypatch.setattr(
        api_tools_routes,
        "resolve_runtime_manager",
        lambda config_path, runtime: (None, fake_runtime, False),
    )
    monkeypatch.setattr(
        api_tools_routes,
        "v1_tool_schema",
        lambda runtime, tool_name: {"name": tool_name},
    )
    monkeypatch.setattr(
        api_tools_operations,
        "_tool_result_artifact_refs",
        lambda trace_id, session_id, result: [],
    )

    class _SelectionService:
        def __init__(self, cfg, tools):
            del cfg, tools

        @staticmethod
        def runtime_binding_policy_metadata():
            return {}

    monkeypatch.setattr(api_tools_operations, "ToolSelectionService", _SelectionService)

    route_ctx = APIRouteContext(
        config_path=None,
        runtime=None,
        runtime_bootstrap_error=None,
        request_headers=None,
        request_id="req-ecc-104",
    )
    result = api_tools_routes.handle_request(
        route_ctx,
        method_name="POST",
        path="/v1/tools/weather/run",
        body={"arguments": {}, "session_id": "s"},
        query=None,
    )

    assert result is not None
    assert int(result.status) == 200
    assert captured["context"].metadata["runtime_env"] == {"ECC_104_API": "enabled"}


def test_cli_inproc_tool_run_injects_runtime_env(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeTools:
        @staticmethod
        def provider_spec_for_name(tool_name):
            return ProviderToolSpec(name=tool_name, description="demo", parameters={})

        def execute_calls(self, calls, context):
            del calls
            captured["context"] = context
            return SimpleNamespace(
                results=[
                    ToolExecutionResult(
                        tool_name="weather",
                        ok=True,
                        content="ok",
                    )
                ]
            )

    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.runtime.env = {"ECC_104_CLI": "enabled"}
    fake_runtime = SimpleNamespace(
        config=config,
        tools=_FakeTools(),
        close=lambda: None,
    )

    monkeypatch.setattr(
        cli_tools_commands.APIRuntime,
        "from_config_path",
        staticmethod(lambda config_path: fake_runtime),
    )

    class _SelectionService:
        def __init__(self, cfg, tools):
            del cfg, tools

        @staticmethod
        def runtime_binding_policy_metadata():
            return {}

    monkeypatch.setattr(cli_tools_commands, "ToolSelectionService", _SelectionService)

    payload = cli_tools_commands._inproc_tool_run(
        None,
        tool_name="weather",
        arguments={},
        session_id="session-cli",
    )
    assert payload["ok"] is True
    assert captured["context"].metadata["runtime_env"] == {"ECC_104_CLI": "enabled"}


def test_tool_fallback_builder_injects_runtime_env():
    captured: dict[str, object] = {}

    class _Tools:
        def execute_calls(self, calls, context):
            del calls
            captured["context"] = context
            return SimpleNamespace(results=[])

    class _FallbackHarness(AgentToolFallbacksMixin):
        def __init__(self):
            self._tools = _Tools()
            self._config = SimpleNamespace(
                runtime=SimpleNamespace(env={"ECC_104_FALLBACK": "enabled"})
            )
            self._identity_agent_id = "agent-fallback"
            self._tool_selection = None

        @staticmethod
        def _execute_browser_navigation_fallback(*, tool_name, inbound, context):
            del tool_name, inbound, context
            return None

        @staticmethod
        def _build_direct_fallback_arguments(*, tool_name, spec, inbound):
            del tool_name, spec, inbound
            return {}

    harness = _FallbackHarness()
    inbound = Message(channel="console", target="user", body="test", metadata={})
    harness._execute_direct_tool_fallback(
        tool_name="file.read", spec=None, inbound=inbound
    )
    assert captured["context"].metadata["runtime_env"] == {
        "ECC_104_FALLBACK": "enabled"
    }


def test_os_adapter_runtime_tool_builder_injects_runtime_env():
    captured: dict[str, object] = {}

    class _RuntimeTool:
        def execute(self, *, arguments, context):
            del arguments
            captured["context"] = context
            return ToolExecutionResult(
                tool_name="runtime.echo",
                ok=True,
                content="ok",
            )

    adapter = object.__new__(ToolAdapter)
    adapter.policy = Policy(
        raw={
            "context_metadata": {
                "runtime_env": {
                    "ECC_104_OS_ADAPTER": "enabled",
                }
            }
        }
    )
    adapter.agent_id = "agent-os"

    outcome = ToolAdapter._execute_openminion_runtime_tool(
        adapter,
        tool=_RuntimeTool(),
        tool_name="runtime.echo",
        args={},
        session_id="session-os",
        trace_id="trace-os",
        start_time=time.monotonic(),
    )

    assert outcome["status"] == "success"
    assert captured["context"].metadata["runtime_env"] == {
        "ECC_104_OS_ADAPTER": "enabled"
    }


def test_os_adapter_runtime_tool_builder_injects_agent_id():
    captured: dict[str, object] = {}

    class _RuntimeTool:
        def execute(self, *, arguments, context):
            del arguments
            captured["context"] = context
            return ToolExecutionResult(
                tool_name="runtime.echo",
                ok=True,
                content="ok",
            )

    adapter = object.__new__(ToolAdapter)
    adapter.policy = Policy(raw={})
    adapter.agent_id = "agent-os"

    outcome = ToolAdapter._execute_openminion_runtime_tool(
        adapter,
        tool=_RuntimeTool(),
        tool_name="runtime.echo",
        args={},
        session_id="session-os",
        trace_id="trace-os",
        start_time=time.monotonic(),
    )

    assert outcome["status"] == "success"
    assert captured["context"].metadata["agent_id"] == "agent-os"


def test_os_adapter_runtime_tool_builder_injects_policy_replay_confirmation():
    captured: dict[str, object] = {}

    class _RuntimeTool:
        def execute(self, *, arguments, context):
            del arguments
            captured["context"] = context
            return ToolExecutionResult(
                tool_name="runtime.echo",
                ok=True,
                content="ok",
            )

    adapter = object.__new__(ToolAdapter)
    adapter.policy = Policy(raw={})
    adapter.agent_id = "agent-os"

    outcome = ToolAdapter._execute_openminion_runtime_tool(
        adapter,
        tool=_RuntimeTool(),
        tool_name="runtime.echo",
        args={},
        session_id="session-os",
        trace_id="trace-os",
        start_time=time.monotonic(),
        replay_confirmation_metadata={
            "confirmation_source": "policy_replay",
            "confirmation_grant_id": "local-confirmation-test",
        },
    )

    assert outcome["status"] == "success"
    assert captured["context"].metadata["confirmation_source"] == "policy_replay"
    assert (
        captured["context"].metadata["confirmation_grant_id"]
        == "local-confirmation-test"
    )


def test_os_adapter_task_cancel_respects_configured_agent_id(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMINION_HOME", str(tmp_path))
    monkeypatch.delenv("OPENMINION_DATA_ROOT", raising=False)

    runtime_env = {"OPENMINION_HOME": str(tmp_path)}
    storage_path = resolve_database_path(None, env=runtime_env)
    brain_db_path = resolve_brain_sessions_db_path(storage_path=storage_path)
    repo = create_sqlite_cron_repository(db_path=brain_db_path)
    manager = TaskManager.from_cron_repository(
        repo,
        db_path=getattr(repo, "db_path", None),
    )
    task = manager.schedule_task(
        name="cancel-me",
        schedule={"kind": "every", "every_ms": 60_000},
        payload={"kind": "agentTurn", "message": "cancel me"},
        agent_id="agent-cancel",
        session_target="isolated",
        delete_after_run=False,
        misfire_policy="skip",
    )

    registry = ToolRegistry()
    from openminion.tools.task.plugin import register as register_task_plugin

    register_task_plugin(registry)

    with patch(
        "openminion.modules.tool.build_default_tool_registry",
        return_value=registry,
    ):
        adapter = ToolAdapter(
            workspace_root=tmp_path,
            policy=Policy(raw={"context_metadata": {"runtime_env": runtime_env}}),
            agent_id="agent-cancel",
        )
        result = adapter.execute(
            command={
                "tool_name": "task.cancel",
                "args": {"task_id": task.task_id},
            },
            session_id="sess-cancel",
            trace_id="trace-cancel",
        )

    assert result["status"] == "success"
    assert repo.get_cron_job(task.task_id) is None
