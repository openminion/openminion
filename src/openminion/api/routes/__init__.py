from __future__ import annotations

from typing import Any

from .contracts import APIRouteContext, RouteResult

_HANDLER_MODULES = {
    "handle_admin_request": ".admin",
    "handle_agent_request": ".agent",
    "handle_cron_request": ".cron",
    "handle_debug_request": ".debug",
    "handle_health_request": ".health",
    "handle_memory_request": ".memory",
    "handle_runtime_request": ".runtime",
    "handle_sessions_request": ".sessions",
    "handle_skill_request": ".skill",
    "handle_tasks_request": ".tasks",
    "handle_tools_request": ".tools",
    "handle_turns_request": ".turns",
}


def __getattr__(name: str) -> Any:
    module_name = _HANDLER_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    from importlib import import_module

    handler = import_module(module_name, __name__).handle_request
    globals()[name] = handler
    return handler


__all__ = [
    "APIRouteContext",
    "RouteResult",
    "handle_admin_request",
    "handle_agent_request",
    "handle_cron_request",
    "handle_debug_request",
    "handle_health_request",
    "handle_memory_request",
    "handle_runtime_request",
    "handle_sessions_request",
    "handle_skill_request",
    "handle_tasks_request",
    "handle_tools_request",
    "handle_turns_request",
]
