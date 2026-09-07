"""MCP runtime observability report assembly."""

from typing import Any


def build_mcp_section(runtime: Any) -> dict[str, Any]:
    manager = runtime.tools.mcp_manager
    if manager is None:
        return {
            "enabled": False,
            "failed_servers": {},
            "server_metrics": {},
            "server_logs": {},
            "resource_updates": {},
            "sampling_events": [],
            "elicitation_events": [],
            "discovery_cache": {},
            "capability_change_events": [],
        }
    failed_servers = {
        str(name): {
            "reason_code": error.reason_code,
            "message": error.message,
        }
        for name, error in manager.failed_servers.items()
    }
    raw_logs = manager.mcp_server_logs(limit=5)
    logs = {
        str(server_name): [
            {
                "level": item.level,
                "message": item.message,
                "logger": item.logger,
                "data": dict(item.data),
                "timestamp": item.timestamp,
            }
            for item in list(items or [])
        ]
        for server_name, items in raw_logs.items()
    }
    raw_updates = manager.mcp_resource_updates(limit=10)
    updates = {
        str(server_name): [
            {
                "uri": item.uri,
                "title": item.title,
                "timestamp": item.timestamp,
            }
            for item in list(items or [])
        ]
        for server_name, items in raw_updates.items()
    }
    return {
        "enabled": True,
        "failed_servers": failed_servers,
        "server_metrics": manager.mcp_server_metrics(),
        "server_logs": logs,
        "resource_updates": updates,
        "sampling_events": manager.mcp_sampling_events(),
        "elicitation_events": manager.mcp_elicitation_events(),
        "discovery_cache": manager.discovery_cache_snapshot(),
        "capability_change_events": manager.capability_change_events(),
    }


__all__ = ["build_mcp_section"]
