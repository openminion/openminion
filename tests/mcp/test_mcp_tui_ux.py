from __future__ import annotations

from types import SimpleNamespace

from openminion.base.config.mcp import MCPServerConfig
from openminion.cli.interactive.mcp_status import (
    MCPServerStatusRow,
    build_mcp_reference,
    render_mcp_status_report,
)
from openminion.cli.interactive.runtime import OpenMinionRuntime


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


def test_mcp_status_renders_call_activity() -> None:
    rendered = render_mcp_status_report(
        [
            MCPServerStatusRow(
                name="fixture",
                transport="stdio",
                status="ready",
                call_total=2,
                call_error_total=1,
                restart_total=0,
            )
        ]
    )

    assert "activity: calls=2 errors=1 restarts=0" in rendered


def test_mcp_reference_builder_is_explicit() -> None:
    assert (
        build_mcp_reference(
            kind="resource",
            server_name="fixture",
            name="ui://widget/card",
        )
        == "mcp://fixture/resource/ui://widget/card"
    )


class _FakeRuntime:
    def __init__(self) -> None:
        self.tools = SimpleNamespace(
            mcp_manager=SimpleNamespace(
                browse_snapshot=lambda: {
                    "fixture": {
                        "prompts": ("daily-summary",),
                        "resources": ("ui://widget/card",),
                        "resource_templates": ("file:///{path}",),
                    }
                }
            )
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


def test_runtime_provider_status_uses_snapshot_without_live_discovery() -> None:
    manager = SimpleNamespace(
        server_status_snapshot=lambda: {
            "fixture": {
                "tool_names": ("echo",),
                "prompt_names": (),
                "resource_uris": (),
                "resource_template_uris": (),
                "failure": None,
                "recent_log": None,
                "metrics": {},
            }
        }
    )
    runtime = object.__new__(OpenMinionRuntime)
    runtime._rt = SimpleNamespace(  # noqa: SLF001
        config=SimpleNamespace(
            runtime=SimpleNamespace(
                mcp_servers=[MCPServerConfig(name="Fixture", command=["fixture"])]
            )
        ),
        tools=SimpleNamespace(mcp_manager=manager, list=lambda: {}),
    )

    rows = runtime.mcp_status_rows()

    assert len(rows) == 1
    assert rows[0].status == "ready"
    assert rows[0].tool_names == ("echo",)
