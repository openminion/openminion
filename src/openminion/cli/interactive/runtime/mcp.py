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


def _mcp_runtime_auxiliary_counts(
    tool_specs: Mapping[str, Any],
    *,
    server_name: str,
) -> tuple[int, int]:
    prompt_prefix = f"mcp.{server_name}.prompt."
    resource_prefix = f"mcp.{server_name}.resource."
    return (
        sum(1 for name in tool_specs if name.startswith(prompt_prefix)),
        sum(1 for name in tool_specs if name.startswith(resource_prefix)),
    )


class RuntimeMCPMixin:
    _rt: Any

    def mcp_status_rows(self) -> list[MCPServerStatusRow]:
        runtime_config = getattr(getattr(self._rt, "config", None), "runtime", None)
        configured_servers = list(getattr(runtime_config, "mcp_servers", []) or [])
        if not configured_servers:
            return []

        tool_specs = dict(self._rt.tools.list())
        manager = getattr(self._rt.tools, "mcp_manager", None)
        status_snapshot = (
            manager.server_status_snapshot() if manager is not None else {}
        )
        rows: list[MCPServerStatusRow] = []
        for server in configured_servers:
            server_name = str(getattr(server, "name", "") or "").strip()
            transport = str(getattr(server, "transport", "") or "stdio").strip()
            tool_names = _mcp_runtime_tool_names(tool_specs, server_name=server_name)
            prompt_count, resource_count = _mcp_runtime_auxiliary_counts(
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
            live = status_snapshot.get(server_name)
            if live is not None:
                tool_names = list(live["tool_names"])
                tool_count = len(tool_names)
                prompt_count = len(live["prompt_names"])
                resource_count = len(live["resource_uris"])
                resource_template_count = len(live["resource_template_uris"])
                app_resource_count = sum(
                    uri.startswith("ui://") for uri in live["resource_uris"]
                )
                failure = live["failure"]
                status = "error" if failure is not None else "ready"
                error = failure.message if failure is not None else ""
            recent_log = ""
            latest_log = live["recent_log"] if live is not None else None
            if latest_log is not None:
                recent_log = (
                    f"{latest_log.level or 'info'}: {latest_log.message}".strip()
                )
            server_metrics = live["metrics"] if live is not None else {}
            sandbox = getattr(server, "stdio_sandbox", None)
            trust_state = (
                "trusted" if bool(getattr(server, "trusted", False)) else "untrusted"
            )
            sandbox_state = (
                "enforced"
                if bool(getattr(sandbox, "require_trust", False))
                or bool(getattr(sandbox, "cwd_allowlist", ()))
                or bool(getattr(sandbox, "env_allowlist", ()))
                or bool(getattr(sandbox, "inherit_env_allowlist", ()))
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
                    call_total=int(server_metrics.get("call_total", 0) or 0),
                    call_error_total=int(
                        server_metrics.get("call_error_total", 0) or 0
                    ),
                    restart_total=int(server_metrics.get("restart_total", 0) or 0),
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
        if manager is None:
            return []
        entries: list[MCPBrowseEntry] = []
        for server_name, catalog in sorted(manager.browse_snapshot().items()):
            for name in catalog["prompts"]:
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
            for uri in catalog["resources"]:
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
            for uri_template in catalog["resource_templates"]:
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
