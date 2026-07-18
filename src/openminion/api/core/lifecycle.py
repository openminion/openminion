"""Close API runtime resources in dependency-safe order."""

from __future__ import annotations

from contextlib import suppress
from collections.abc import Callable
from typing import Protocol, cast
import weakref


class RuntimeFinalizer(Protocol):
    @property
    def alive(self) -> bool: ...

    def detach(self) -> object: ...


class _ExposureService(Protocol):
    def bind_event_sink(
        self,
        sink: Callable[[dict[str, object]], None],
    ) -> None: ...


def _call(resource: object | None, method: str, **kwargs: object) -> None:
    callback = getattr(resource, method, None)
    if callable(callback):
        with suppress(Exception):
            callback(**kwargs)


def initialize_runtime_components(
    runtime: object,
    *,
    tool_exposure_event_sink: Callable[[dict[str, object]], None],
) -> RuntimeFinalizer:
    runtime_tools = getattr(runtime, "tools", None)
    channel_supervisor = getattr(runtime, "channel_supervisor", None)
    exposure_service = cast(
        _ExposureService,
        getattr(runtime_tools, "exposure_service", None),
    )
    exposure_service.bind_event_sink(tool_exposure_event_sink)
    _call(channel_supervisor, "start")
    return weakref.finalize(
        runtime,
        close_runtime_components,
        channel_supervisor=channel_supervisor,
        retrieve_ctl=getattr(runtime, "retrieve_ctl", None),
        action_policy=getattr(runtime, "action_policy", None),
        runtime_manager=getattr(runtime, "runtime_manager", None),
        lifecycle_bridge=getattr(runtime, "_lifecycle_event_bridge", None),
        tools=runtime_tools,
        runtime_storage=getattr(runtime, "runtime_storage", None),
        sandbox_runner=getattr(runtime, "sandbox_runner", None),
        authored_tools=getattr(runtime, "authored_tools", None),
        telemetry_service=getattr(runtime, "telemetry_service", None),
    )


def close_runtime_components(
    *,
    channel_supervisor: object | None = None,
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
    _call(channel_supervisor, "stop")
    _call(retrieve_ctl, "close")
    _call(action_policy, "close")
    _call(runtime_manager, "shutdown", grace_s=2)
    _call(lifecycle_bridge, "close")
    _call(sandbox_runner, "close")
    _call(authored_tools, "close")
    _call(getattr(tools, "mcp_manager", None), "close")
    _call(runtime_storage, "close")
    _call(telemetry_service, "close_sync")
