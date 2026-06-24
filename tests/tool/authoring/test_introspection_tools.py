from __future__ import annotations

from openminion.modules.llm.providers.base import ProviderToolCall
from openminion.modules.tool import ToolExecutionContext, ToolRegistry
from openminion.modules.tool.authoring.schemas import AuthoredToolRow
from openminion.tools.tool_authoring.plugin import (
    register as register_tool_authoring_tools,
)
from openminion.tools.tool_catalog.plugin import register as register_tool_catalog_tools

from ._helpers import build_service


def _row(
    tool_name: str,
    *,
    tier: str = "experimental",
    version_number: int = 1,
    removed_at: str | None = None,
) -> AuthoredToolRow:
    return AuthoredToolRow(
        tool_name=tool_name,
        local_name="adder",
        version_number=version_number,
        version_hash=f"hash-{tier}-{version_number}",
        source_code="def adder(x, y):\n    return x + y\n",
        unit_tests_source="def test_add():\n    assert True\n",
        args_schema_json='{"type":"object","properties":{"x":{"type":"integer"},"y":{"type":"integer"}},"required":["x","y"]}',
        returns_schema_json='{"type":"integer"}',
        description=f"{tier} tool",
        dependencies_json="[]",
        tier=tier,
        min_scope="POWER_USER",
        policy_grant_id="grant-1",
        created_at="2026-05-21T00:00:00Z",
        updated_at="2026-05-21T00:00:00Z",
        created_by_agent_id="agent-1",
        promoted_at=None,
        promoted_by=None,
        success_count=3,
        failure_count=1,
        last_invocation_at="2026-05-21T00:05:00Z",
        removed_at=removed_at,
        removed_by="toolctl" if removed_at else None,
    )


def _execute(
    registry: ToolRegistry, *, service, tool_name: str, arguments: dict
) -> dict:
    batch = registry.execute_calls(
        [
            ProviderToolCall(
                name=tool_name,
                arguments=arguments,
                id="call-1",
                source="test",
            )
        ],
        context=ToolExecutionContext(
            channel="console",
            target="test",
            session_id="sess-1",
            authored_tools_api=service,
        ),
    )
    return batch.results[0].data if batch.results else {}


def test_tool_list_library_filters_authored_rows(tmp_path) -> None:
    registry = ToolRegistry()
    register_tool_catalog_tools(registry)
    service = build_service(tmp_path, registry=registry)
    try:
        service._store.insert_authored_tool(
            _row("authored.adder@v1", tier="experimental", version_number=1)
        )  # noqa: SLF001
        service._store.insert_authored_tool(
            _row("authored.adder@v2", tier="trusted", version_number=2)
        )  # noqa: SLF001
        service._store.insert_authored_tool(  # noqa: SLF001
            _row(
                "authored.adder@v3",
                tier="trusted",
                version_number=3,
                removed_at="2026-05-21T01:00:00Z",
            )
        )
        payload = _execute(
            registry,
            service=service,
            tool_name="tool.list",
            arguments={"library": True, "tier": "trusted"},
        )
        assert payload["count"] == 1
        assert payload["tools"][0]["tool_name"] == "authored.adder@v2"
    finally:
        service.close()


def test_tool_get_returns_full_authored_tool_detail(tmp_path) -> None:
    registry = ToolRegistry()
    register_tool_authoring_tools(registry)
    service = build_service(tmp_path, registry=registry)
    try:
        service._store.insert_authored_tool(_row("authored.adder@v1"))  # noqa: SLF001
        payload = _execute(
            registry,
            service=service,
            tool_name="tool.get",
            arguments={"tool_name": "authored.adder@v1"},
        )
        assert payload["tool"]["tool_name"] == "authored.adder@v1"
        assert payload["tool"]["source_code"].startswith("def adder")
        assert payload["tool"]["dependencies"] == []
    finally:
        service.close()
