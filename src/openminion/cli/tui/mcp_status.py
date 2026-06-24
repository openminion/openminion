from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MCPServerStatusRow:
    name: str
    transport: str
    status: str
    tool_count: int = 0
    prompt_count: int = 0
    resource_count: int = 0
    resource_template_count: int = 0
    app_resource_count: int = 0
    tool_names: tuple[str, ...] = ()
    error: str = ""
    recent_log: str = ""
    trust_state: str = ""
    sandbox_state: str = ""


@dataclass(frozen=True)
class MCPBrowseEntry:
    kind: str
    server_name: str
    name: str
    reference: str
    ui_resource: bool = False
    fallback: str = ""


def render_mcp_status_report(rows: list[MCPServerStatusRow]) -> str:
    if not rows:
        return "No MCP servers configured."
    lines = ["MCP servers:"]
    for row in rows:
        summary = (
            f"- {row.name}  [{row.status}]  transport={row.transport}  "
            f"tools={row.tool_count}  prompts={row.prompt_count}  "
            f"resources={row.resource_count}  templates={row.resource_template_count}"
        )
        lines.append(summary)
        if row.app_resource_count:
            lines.append(
                "  apps: "
                f"{row.app_resource_count} ui:// resource(s), text-only fallback"
            )
        if row.tool_names:
            rendered_tools = ", ".join(row.tool_names[:5])
            remainder = max(0, len(row.tool_names) - 5)
            if remainder:
                rendered_tools = f"{rendered_tools}, +{remainder} more"
            lines.append(f"  tools: {rendered_tools}")
        if row.error:
            lines.append(f"  error: {row.error}")
        if row.recent_log:
            lines.append(f"  recent log: {row.recent_log}")
        if row.trust_state or row.sandbox_state:
            lines.append(
                "  security: "
                f"trust={row.trust_state or 'unspecified'} "
                f"sandbox={row.sandbox_state or 'default'}"
            )
    return "\n".join(lines)


def build_mcp_reference(*, kind: str, server_name: str, name: str) -> str:
    safe_kind = str(kind or "resource").strip().lower() or "resource"
    return f"mcp://{server_name}/{safe_kind}/{name}"


__all__ = [
    "MCPBrowseEntry",
    "MCPServerStatusRow",
    "build_mcp_reference",
    "render_mcp_status_report",
]
