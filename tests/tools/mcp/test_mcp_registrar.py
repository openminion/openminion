from __future__ import annotations

import sys
from pathlib import Path

from openminion.base.config.mcp import MCPServerConfig
from openminion.base.config.runtime import RuntimeConfig
from openminion.modules.tool.bootstrap import build_runtime_bootstrap
from openminion.modules.tool.runtime.registrar import ToolRegisterContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.tools.mcp import REGISTRAR
from openminion.tools.mcp.interfaces import MCPToolRegistrationState


FIXTURE_SERVER_PATH = (
    Path(__file__).resolve().parents[2] / "mcp" / "fixtures" / "mock_mcp_server.py"
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


def _registration_context() -> tuple[ToolRegisterContext, MCPToolRegistrationState]:
    bootstrap = build_runtime_bootstrap(config=_runtime_config(), strict=True)
    manager = bootstrap.mcp_manager
    assert manager is not None
    state = MCPToolRegistrationState(
        manager=manager,
        discovered_tools=tuple(manager.discover_tools()),
        discovered_prompts=tuple(manager.discover_prompts()),
        discovered_resources=tuple(manager.discover_resources()),
        discovered_resource_templates=tuple(manager.discover_resource_templates()),
    )
    ctx = ToolRegisterContext(
        module_id="mcp",
        config=_runtime_config(),
        prepared_state=state,
        strict=True,
    )
    return ctx, state


def _close_ctx(ctx: ToolRegisterContext) -> None:
    state = getattr(ctx, "prepared_state", None)
    manager = getattr(state, "manager", None)
    if manager is not None:
        manager.close()


def test_mcp_registrar_returns_manifest_from_shared_snapshot() -> None:
    ctx, state = _registration_context()
    try:
        manifest = REGISTRAR.get_manifest(ctx)
        model_ids = {tool.model_tool_id for tool in manifest.model_tools}
        runtime_ids = {
            binding.runtime_binding_id for binding in manifest.runtime_bindings
        }

        assert model_ids == {
            "mcp.fixture.echo_text",
            "mcp.fixture.add_numbers",
            "mcp.fixture.emit_list_changed",
            "mcp.fixture.nullable_anyof",
            "mcp.fixture.sleep_tool",
            "mcp.fixture.stderr_error_tool",
            "mcp.fixture.tagged_union_simple",
            "mcp.fixture.prompt.greet_user",
            "mcp.fixture.resource.readme_md",
            "mcp.fixture.resource_template.fixture_doc",
            "mcp.fixture.unsupported_anyof",
        }
        assert runtime_ids == {
            "runtime.mcp.fixture.echo_text",
            "runtime.mcp.fixture.add_numbers",
            "runtime.mcp.fixture.emit_list_changed",
            "runtime.mcp.fixture.nullable_anyof",
            "runtime.mcp.fixture.sleep_tool",
            "runtime.mcp.fixture.stderr_error_tool",
            "runtime.mcp.fixture.tagged_union_simple",
            "runtime.mcp.fixture.prompt.greet_user",
            "runtime.mcp.fixture.resource.readme_md",
            "runtime.mcp.fixture.resource_template.fixture_doc",
            "runtime.mcp.fixture.unsupported_anyof",
        }
        assert "passthrough_mcp_tools=" in state.error_summary
    finally:
        _close_ctx(ctx)


def test_mcp_registrar_register_uses_same_snapshot_as_manifest() -> None:
    ctx, state = _registration_context()
    registry = ToolRegistry([])
    try:
        manifest = REGISTRAR.get_manifest(ctx)
        REGISTRAR.register(registry, ctx)

        runtime_tools = set(registry.list().keys())
        manifest_candidates = {
            candidate
            for binding in manifest.runtime_bindings
            for candidate in binding.runtime_candidates
        }
        assert runtime_tools == manifest_candidates
        assert runtime_tools == set(state.added_runtime_tools)
        assert registry.mcp_manager is None
    finally:
        _close_ctx(ctx)
