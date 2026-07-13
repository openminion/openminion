"""Close API runtime resources in dependency-safe order."""

from __future__ import annotations

from contextlib import suppress


def _call(resource: object | None, method: str, **kwargs: object) -> None:
    callback = getattr(resource, method, None)
    if callable(callback):
        with suppress(Exception):
            callback(**kwargs)


def close_runtime_components(
    *,
    retrieve_ctl: object | None,
    action_policy: object | None,
    runtime_manager: object | None,
    lifecycle_bridge: object | None,
    tools: object | None,
    runtime_storage: object | None,
    sandbox_runner: object | None = None,
    authored_tools: object | None = None,
    telemetry_service: object | None = None,
) -> None:
    _call(retrieve_ctl, "close")
    _call(action_policy, "close")
    _call(runtime_manager, "shutdown", grace_s=2)
    _call(lifecycle_bridge, "close")
    _call(sandbox_runner, "close")
    _call(authored_tools, "close")
    _call(getattr(tools, "mcp_manager", None), "close")
    _call(runtime_storage, "close")
    _call(telemetry_service, "close_sync")
