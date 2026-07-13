"""MCP runtime observability report assembly."""

from __future__ import annotations

from typing import Any


def build_mcp_section(runtime: Any) -> dict[str, Any]:
    manager = getattr(getattr(runtime, "tools", None), "mcp_manager", None)
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
            "reason_code": str(getattr(error, "reason_code", "") or "").strip(),
            "message": str(getattr(error, "message", "") or "").strip(),
        }
        for name, error in dict(getattr(manager, "failed_servers", {}) or {}).items()
    }
    raw_logs = _call(manager, "mcp_server_logs", limit=5, default={})
    logs = {
        str(server_name): [
            {
                "level": str(getattr(item, "level", "") or "").strip(),
                "message": str(getattr(item, "message", "") or "").strip(),
                "logger": str(getattr(item, "logger", "") or "").strip(),
                "data": dict(getattr(item, "data", {}) or {}),
                "timestamp": float(getattr(item, "timestamp", 0.0) or 0.0),
            }
            for item in list(items or [])
        ]
        for server_name, items in dict(raw_logs or {}).items()
    }
    raw_updates = _call(manager, "mcp_resource_updates", limit=10, default={})
    updates = {
        str(server_name): [
            {
                "uri": str(getattr(item, "uri", "") or "").strip(),
                "title": str(getattr(item, "title", "") or "").strip(),
                "timestamp": float(getattr(item, "timestamp", 0.0) or 0.0),
            }
            for item in list(items or [])
        ]
        for server_name, items in dict(raw_updates or {}).items()
    }
    return {
        "enabled": True,
        "failed_servers": failed_servers,
        "server_metrics": dict(_call(manager, "mcp_server_metrics", default={}) or {}),
        "server_logs": logs,
        "resource_updates": updates,
        "sampling_events": list(
            _call(manager, "mcp_sampling_events", default=[]) or []
        ),
        "elicitation_events": list(
            _call(manager, "mcp_elicitation_events", default=[]) or []
        ),
        "discovery_cache": dict(
            _call(manager, "discovery_cache_snapshot", default={}) or {}
        ),
        "capability_change_events": list(
            _call(manager, "capability_change_events", default=[]) or []
        ),
    }


def _call(manager: Any, name: str, *, default: Any, **kwargs: Any) -> Any:
    callback = getattr(manager, name, None)
    return callback(**kwargs) if callable(callback) else default


__all__ = ["build_mcp_section"]
