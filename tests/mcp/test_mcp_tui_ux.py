from __future__ import annotations

from types import SimpleNamespace

from openminion.cli.tui.mcp_status import (
    MCPServerStatusRow,
    build_mcp_reference,
    render_mcp_status_report,
)
from openminion.cli.tui.providers.runtime import OpenMinionRuntime
from openminion.tools.mcp.schemas import (
    MCPListedPrompt,
    MCPListedResource,
    MCPListedResourceTemplate,
)


def test_mcp_status_renders_resource_templates_and_apps_fallback() -> None:
    rendered = render_mcp_status_report(
        [
            MCPServerStatusRow(
                name="fixture",
                transport="stdio",
                status="ready",
                prompt_count=1,
                resource_count=2,
                resource_template_count=1,
                app_resource_count=1,
            )
        ]
    )

    assert "templates=1" in rendered
    assert "ui:// resource(s), text-only fallback" in rendered


def test_mcp_reference_builder_is_explicit() -> None:
    assert (
        build_mcp_reference(
            kind="resource",
            server_name="fixture",
            name="ui://widget/card",
        )
        == "mcp://fixture/resource/ui://widget/card"
    )


class _FakeSession:
    def list_prompts(self):
        return [
            MCPListedPrompt(
                server_name="fixture",
                remote_name="daily-summary",
                description="",
                arguments_schema={"type": "object", "properties": {}},
            )
        ]

    def list_resources(self):
        return [
            MCPListedResource(
                server_name="fixture",
                resource_uri="ui://widget/card",
                resource_name="Card",
                description="",
                mime_type="text/html",
            )
        ]

    def list_resource_templates(self):
        return [
            MCPListedResourceTemplate(
                server_name="fixture",
                uri_template="file:///{path}",
                template_name="file",
                description="",
                mime_type="text/plain",
                arguments_schema={"type": "object", "properties": {}},
            )
        ]


class _FakeRuntime:
    def __init__(self) -> None:
        self.tools = SimpleNamespace(
            mcp_manager=SimpleNamespace(_sessions={"fixture": _FakeSession()})
        )
        self.config = SimpleNamespace(runtime=SimpleNamespace(mcp_servers=[]))


def test_runtime_provider_mcp_browse_entries_expose_ui_fallback() -> None:
    runtime = object.__new__(OpenMinionRuntime)
    runtime._rt = _FakeRuntime()  # noqa: SLF001

    entries = runtime.mcp_browse_entries()

    assert {entry.kind for entry in entries} == {
        "prompt",
        "resource",
        "resource_template",
    }
    ui_entry = next(entry for entry in entries if entry.ui_resource)
    assert ui_entry.fallback == "text-only"
    assert ui_entry.reference == "mcp://fixture/resource/ui://widget/card"
