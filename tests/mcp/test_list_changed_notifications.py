from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.schema_service import ToolSchemaService
from openminion.tools.mcp.interfaces import MCPCapabilityChangeListener
from openminion.tools.mcp.manager import MCPFleetManager
from openminion.tools.mcp.schemas import (
    MCPListedResource,
    MCPListedResourceTemplate,
    MCPListedTool,
)


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
)


class _Listener(MCPCapabilityChangeListener):
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def capability_changed(
        self,
        *,
        server_name: str,
        primitive: str,
        added: tuple[str, ...] = (),
        removed: tuple[str, ...] = (),
    ) -> None:
        with self._lock:
            self.events.append(
                {
                    "server_name": server_name,
                    "primitive": primitive,
                    "added": tuple(added),
                    "removed": tuple(removed),
                }
            )


def _runtime_config() -> RuntimeConfig:
    return RuntimeConfig(
        mcp_servers=[
            MCPServerConfig(
                name="Fixture",
                transport="stdio",
                command=[sys.executable, str(FIXTURE_SERVER_PATH)],
                request_timeout_seconds=5.0,
                startup_timeout_seconds=5.0,
            )
        ]
    )


def _wait_for(predicate, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for capability-change event")


def test_list_changed_notifications_route_each_primitive_to_listener() -> None:
    listener = _Listener()
    manager = MCPFleetManager(
        servers=_runtime_config().mcp_servers,
        capability_change_listener=listener,
    )
    try:
        manager.discover_tools()
        manager.discover_prompts()
        manager.discover_resources()
        session = manager._sessions["fixture"]

        session._handle_server_notification(
            method="notifications/tools/list_changed",
            params={},
        )
        session._handle_server_notification(
            method="notifications/prompts/list_changed",
            params={},
        )
        session._handle_server_notification(
            method="notifications/resources/list_changed",
            params={},
        )

        def _saw_all() -> bool:
            primitives = {item["primitive"] for item in listener.events}
            return primitives >= {"tools", "prompts", "resources"}

        _wait_for(_saw_all)
    finally:
        manager.close()


def test_emit_list_changed_refreshes_catalog_and_reports_added_tool() -> None:
    listener = _Listener()
    manager = MCPFleetManager(
        servers=_runtime_config().mcp_servers,
        capability_change_listener=listener,
    )
    try:
        discovered = manager.discover_tools()
        assert all(tool.remote_name != "dynamic-after-change" for tool in discovered)

        result = manager.call_tool(
            server_name="fixture",
            remote_name="emit-list-changed",
            arguments={},
        )
        assert result["ok"] is True

        def _dynamic_added() -> bool:
            return any(
                item["primitive"] == "tools" and "dynamic-after-change" in item["added"]
                for item in listener.events
            )

        _wait_for(_dynamic_added)
        events = manager.capability_change_events()
        assert any(
            item["primitive"] == "tools" and "dynamic-after-change" in item["added"]
            for item in events
        )
    finally:
        manager.close()


def test_list_changed_hot_reload_updates_live_registry_and_hides_removed_tool() -> None:
    bootstrap = build_runtime_bootstrap(config=_runtime_config(), strict=True)
    try:
        registry = bootstrap.registry
        manager = bootstrap.mcp_manager
        assert manager is not None
        assert "mcp.fixture.dynamic_after_change" not in registry.list()

        result = manager.call_tool(
            server_name="fixture",
            remote_name="emit-list-changed",
            arguments={},
        )
        assert result["ok"] is True

        def _registered() -> bool:
            return "mcp.fixture.dynamic_after_change" in registry.list()

        _wait_for(_registered)
        schema_names = {
            str(item.get("name", "") or "")
            for item in ToolSchemaService().collect_execution_tool_schemas(
                registry=registry
            )
        }
        assert "mcp.fixture.dynamic_after_change" in schema_names

        batch = registry.execute_calls(
            [
                ProviderToolCall(
                    name="mcp.fixture.dynamic_after_change",
                    arguments={},
                    source="native",
                )
            ],
            context=ToolExecutionContext(
                channel="console",
                target="unit-test",
                session_id="session-hot-reload",
                metadata={"tool_call_origin": "model"},
            ),
        )
        assert batch.results[0].ok is True
        assert batch.results[0].content.startswith("dynamic")

        result = manager.call_tool(
            server_name="fixture",
            remote_name="emit-list-changed",
            arguments={},
        )
        assert result["ok"] is True

        def _removed() -> bool:
            return "mcp.fixture.dynamic_after_change" not in registry.list()

        _wait_for(_removed)
        schema_names_after = {
            str(item.get("name", "") or "")
            for item in ToolSchemaService().collect_execution_tool_schemas(
                registry=registry
            )
        }
        assert "mcp.fixture.dynamic_after_change" not in schema_names_after
        try:
            registry.get("mcp.fixture.dynamic_after_change")
        except KeyError:
            pass
        else:
            raise AssertionError("removed MCP tool should not remain invocable")
    finally:
        manager = getattr(bootstrap, "mcp_manager", None)
        if manager is not None:
            manager.close()


def test_resource_and_template_catalogs_are_independent() -> None:
    manager = MCPFleetManager(servers=[])
    manager._update_catalog(  # noqa: SLF001
        primitive="resources",
        server_name="fixture",
        items=[
            MCPListedResource(
                server_name="fixture",
                resource_uri="file://fixture/readme.md",
                resource_name="README",
                description="",
                mime_type="text/markdown",
            )
        ],
    )
    manager._update_catalog(  # noqa: SLF001
        primitive="resource_templates",
        server_name="fixture",
        items=[
            MCPListedResourceTemplate(
                server_name="fixture",
                uri_template="file://fixture/{name}",
                template_name="document",
                description="",
                mime_type="text/plain",
                arguments_schema={},
            )
        ],
    )

    assert manager._catalog_for("resources", "fixture") == (  # noqa: SLF001
        "file://fixture/readme.md",
    )
    assert manager._catalog_for("resource_templates", "fixture") == (  # noqa: SLF001
        "file://fixture/{name}",
    )


def test_duplicate_capability_refreshes_are_coalesced(monkeypatch) -> None:
    manager = MCPFleetManager(servers=_runtime_config().mcp_servers)
    started = threading.Event()
    release = threading.Event()
    calls: list[tuple[str, str]] = []

    def _refresh(*, server_name: str, primitive: str) -> None:
        calls.append((server_name, primitive))
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr(manager, "_refresh_capability_change", _refresh)
    try:
        manager._on_capability_change(  # noqa: SLF001
            server_name="fixture", primitive="tools"
        )
        assert started.wait(timeout=5)
        manager._on_capability_change(  # noqa: SLF001
            server_name="fixture", primitive="tools"
        )
        release.set()
        _wait_for(lambda: not manager._refresh_pending)  # noqa: SLF001

        assert calls == [("fixture", "tools")]
    finally:
        release.set()
        manager.close()


def test_live_registration_failure_is_returned_for_event_reporting() -> None:
    class _RejectingRegistry(ToolRegistry):
        def register(self, tool) -> None:
            raise RuntimeError(f"rejected {tool.name}")

    manager = MCPFleetManager(servers=[])
    manager.attach_registry(_RejectingRegistry())
    errors = manager._apply_live_registry_delta(  # noqa: SLF001
        primitive="tools",
        server_name="fixture",
        items=[
            MCPListedTool(
                server_name="fixture",
                remote_name="new-tool",
                description="",
                input_schema={"type": "object"},
            )
        ],
        added=("new-tool",),
        removed=(),
    )

    assert len(errors) == 1
    assert "mcp.fixture.new_tool:RuntimeError:rejected" in errors[0]
