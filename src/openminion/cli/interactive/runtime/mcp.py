from __future__ import annotations

from typing import Any, Mapping

from openminion.cli.interactive.mcp_status import (
    MCPBrowseEntry,
    MCPServerStatusRow,
    build_mcp_reference,
    render_mcp_status_report,
)


def _mcp_runtime_tool_names(
    tool_specs: Mapping[str, Any],
    *,
    server_name: str,
) -> list[str]:
    prefix = f"mcp.{server_name}."
    prompt_prefix = f"{prefix}prompt."
    resource_prefix = f"{prefix}resource."
    names = [
        name
        for name in tool_specs
        if name.startswith(prefix)
        and not name.startswith(prompt_prefix)
        and not name.startswith(resource_prefix)
    ]
    names.sort()
    return names


def _mcp_runtime_prompt_count(
    tool_specs: Mapping[str, Any],
    *,
    server_name: str,
) -> int:
    prefix = f"mcp.{server_name}.prompt."
    return sum(1 for name in tool_specs if name.startswith(prefix))


def _mcp_runtime_resource_count(
    tool_specs: Mapping[str, Any],
    *,
    server_name: str,
) -> int:
    prefix = f"mcp.{server_name}.resource."
    return sum(1 for name in tool_specs if name.startswith(prefix))


class RuntimeMCPMixin:
    _rt: Any

    def mcp_status_rows(self) -> list[MCPServerStatusRow]:
        runtime_config = getattr(getattr(self._rt, "config", None), "runtime", None)
        configured_servers = list(getattr(runtime_config, "mcp_servers", []) or [])
        if not configured_servers:
            return []

        tool_specs = dict(self._rt.tools.list())
        manager = getattr(self._rt.tools, "mcp_manager", None)
        sessions = getattr(manager, "_sessions", {}) if manager is not None else {}
        log_snapshot = (
            manager.mcp_server_logs(limit=1)
            if manager is not None and hasattr(manager, "mcp_server_logs")
            else {}
        )
        rows: list[MCPServerStatusRow] = []
        for server in configured_servers:
            server_name = str(getattr(server, "name", "") or "").strip()
            transport = str(getattr(server, "transport", "") or "stdio").strip()
            tool_names = _mcp_runtime_tool_names(tool_specs, server_name=server_name)
            prompt_count = _mcp_runtime_prompt_count(
                tool_specs, server_name=server_name
            )
            resource_count = _mcp_runtime_resource_count(
                tool_specs, server_name=server_name
            )
            resource_template_count = 0
            app_resource_count = 0
            tool_count = len(tool_names)
            status = (
                "registered"
                if (tool_count or prompt_count or resource_count)
                else "configured"
            )
            error = ""
            live_session = (
                sessions.get(server_name) if isinstance(sessions, dict) else None
            )
            if live_session is not None:
                try:
                    tool_count = len(live_session.list_tools())
                    prompt_count = len(live_session.list_prompts())
                    resources = live_session.list_resources()
                    resource_count = len(resources)
                    list_templates = getattr(
                        live_session,
                        "list_resource_templates",
                        None,
                    )
                    resource_template_count = (
                        len(list_templates()) if callable(list_templates) else 0
                    )
                    app_resource_count = sum(
                        1
                        for resource in resources
                        if str(getattr(resource, "resource_uri", "") or "").startswith(
                            "ui://"
                        )
                    )
                    status = "ready"
                except Exception as exc:
                    tool_count = max(tool_count, len(tool_names))
                    status = "error"
                    error = str(exc).strip() or exc.__class__.__name__
            recent_log = ""
            server_logs = log_snapshot.get(server_name, []) if log_snapshot else []
            if server_logs:
                latest_log = server_logs[-1]
                recent_log = (
                    f"{latest_log.level or 'info'}: {latest_log.message}".strip()
                )
            sandbox = getattr(server, "stdio_sandbox", None)
            trust_state = (
                "trusted" if bool(getattr(server, "trusted", False)) else "untrusted"
            )
            sandbox_state = (
                "enforced"
                if bool(getattr(sandbox, "require_trust", False))
                or bool(getattr(sandbox, "cwd_allowlist", ()))
                or bool(getattr(sandbox, "env_allowlist", ()))
                else "default"
            )
            rows.append(
                MCPServerStatusRow(
                    name=server_name or "(unnamed)",
                    transport=transport or "stdio",
                    status=status,
                    tool_count=tool_count,
                    prompt_count=prompt_count,
                    resource_count=resource_count,
                    resource_template_count=resource_template_count,
                    app_resource_count=app_resource_count,
                    tool_names=tuple(tool_names),
                    error=error,
                    recent_log=recent_log,
                    trust_state=trust_state,
                    sandbox_state=sandbox_state,
                )
            )
        rows.sort(key=lambda row: row.name)
        return rows

    def mcp_status_report(self) -> str:
        return render_mcp_status_report(self.mcp_status_rows())

    def mcp_browse_entries(self) -> list[MCPBrowseEntry]:
        manager = getattr(self._rt.tools, "mcp_manager", None)
        sessions = getattr(manager, "_sessions", {}) if manager is not None else {}
        if not isinstance(sessions, dict):
            return []
        entries: list[MCPBrowseEntry] = []
        for server_name, session in sorted(sessions.items()):
            try:
                prompts = session.list_prompts()
                resources = session.list_resources()
                templates = session.list_resource_templates()
            except Exception:
                continue
            for prompt in prompts:
                name = str(getattr(prompt, "remote_name", "") or "").strip()
                entries.append(
                    MCPBrowseEntry(
                        kind="prompt",
                        server_name=str(server_name),
                        name=name,
                        reference=build_mcp_reference(
                            kind="prompt", server_name=str(server_name), name=name
                        ),
                    )
                )
            for resource in resources:
                uri = str(getattr(resource, "resource_uri", "") or "").strip()
                is_ui = uri.startswith("ui://")
                entries.append(
                    MCPBrowseEntry(
                        kind="resource",
                        server_name=str(server_name),
                        name=uri,
                        reference=build_mcp_reference(
                            kind="resource", server_name=str(server_name), name=uri
                        ),
                        ui_resource=is_ui,
                        fallback="text-only" if is_ui else "",
                    )
                )
            for template in templates:
                uri_template = str(getattr(template, "uri_template", "") or "").strip()
                entries.append(
                    MCPBrowseEntry(
                        kind="resource_template",
                        server_name=str(server_name),
                        name=uri_template,
                        reference=build_mcp_reference(
                            kind="resource_template",
                            server_name=str(server_name),
                            name=uri_template,
                        ),
                    )
                )
        return entries

    def mcp_complete(
        self,
        *,
        server_name: str,
        ref_type: str,
        ref_name: str,
        argument_name: str,
        argument_value: str = "",
        context_arguments: dict[str, object] | None = None,
    ) -> list[str]:
        manager = getattr(self._rt.tools, "mcp_manager", None)
        if manager is None or not hasattr(manager, "complete"):
            return []
        result = manager.complete(
            server_name=server_name,
            ref_type=ref_type,
            ref_name=ref_name,
            argument_name=argument_name,
            argument_value=argument_value,
            context_arguments=dict(context_arguments or {}),
        )
        return list(getattr(result, "values", ()) or ())
