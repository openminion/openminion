"""Runtime ingress facade and compatibility patch points."""

from typing import TYPE_CHECKING, Any, Callable

from openminion.base.config import combine_run_profile_overrides
from openminion.base.config.core import resolve_default_agent_id

from .execution import (
    _build_turn_context,
    execute_gateway_turn_impl,
    execute_runtime_turn as _execute_runtime_turn_impl,
)
from .gateway_call import run_gateway_once_impl
from .payloads import (
    apply_inbound_overrides,
    mutable_inbound_metadata as _mutable_inbound_metadata,
    parse_forced_tools,
    parse_inbound_metadata,
    resolve_capability_category,
)
from .requests import (
    apply_workspace_root,
    build_manager_turn_request,
    runtime_turn_request_from_manager_request,
    runtime_turn_request_from_payload,
)
from .timeout import _parse_run_profile_overrides, resolve_timeout_seconds
from .timing import _emit_chat_phase_timing
from .types import (
    RuntimeTurnHandle,
    RuntimeTurnRequest,
    RuntimeTurnResult,
    TurnContext,
    TurnRequestError,
    TurnTimeoutError,
)

if TYPE_CHECKING:
    from openminion.api.runtime import APIRuntime

__all__ = [
    "RuntimeTurnHandle",
    "RuntimeTurnRequest",
    "RuntimeTurnResult",
    "TurnRequestError",
    "TurnTimeoutError",
    "_build_turn_context",
    "_mutable_inbound_metadata",
    "apply_workspace_root",
    "apply_inbound_overrides",
    "build_manager_turn_request",
    "execute_runtime_turn",
    "parse_forced_tools",
    "parse_inbound_metadata",
    "resolve_capability_category",
    "resolve_timeout_seconds",
    "run_turn_payload",
    "runtime_turn_request_from_manager_request",
    "runtime_turn_request_from_payload",
    "submit_turn_payload",
]


def run_turn_payload(
    *,
    runtime: "APIRuntime",
    payload: dict[str, Any],
    request_id: str | None = None,
    progress_callback: Callable[[object], None] | None = None,
    approval_callback: Any | None = None,
) -> dict[str, Any]:
    from openminion.modules.telemetry.trace.phase_timing import ChatPhaseTimer

    cold_start = bool(payload.get("__crtl_cold_start__", False))
    timer = ChatPhaseTimer(cold_start=cold_start)
    request = runtime_turn_request_from_payload(
        runtime=runtime,
        payload=payload,
        request_id=request_id,
    )
    try:
        result = execute_runtime_turn(
            runtime=runtime,
            request=request,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
        )
        return result.as_payload()
    finally:
        _emit_chat_phase_timing(runtime=runtime, timer=timer, request=request)


def submit_turn_payload(
    *,
    runtime: "APIRuntime",
    payload: dict[str, Any],
) -> RuntimeTurnHandle:
    manager = getattr(runtime, "runtime_manager", None)
    if manager is None:
        raise RuntimeError("runtime manager is unavailable")
    request = build_manager_turn_request(
        payload,
        default_agent_id=resolve_default_agent_id(runtime.config),
    )
    timeout_s = resolve_timeout_seconds(
        payload=payload,
        default_seconds=runtime.config.gateway.api_turn_timeout_seconds,
        config=runtime.config,
        agent_id=request.agent_id or None,
        run_profile_overrides=combine_run_profile_overrides(
            getattr(runtime, "run_profile_overrides", None),
            _parse_run_profile_overrides(payload),
        ),
    )
    handle = manager.submit_turn(request)
    return RuntimeTurnHandle(
        request=request,
        handle=handle,
        timeout_s=timeout_s,
    )


def execute_runtime_turn(
    *,
    runtime: "APIRuntime",
    request: RuntimeTurnRequest,
    progress_callback: Callable[[object], None] | None = None,
    approval_callback: Any | None = None,
) -> RuntimeTurnResult:
    return _execute_runtime_turn_impl(
        runtime=runtime,
        request=request,
        run_gateway_once=_run_gateway_once,
        progress_callback=progress_callback,
        approval_callback=approval_callback,
    )


def _execute_gateway_turn(
    *,
    runtime: Any,
    agent_name: str,
    channel: str,
    target: str,
    context: TurnContext,
    session_id: str | None,
    idempotency_key: str | None,
    request_id: str | None,
    deliver: bool,
    capability_category: str | None,
    timeout_seconds: float,
    run_profile_overrides: Any,
    progress_callback: Callable[[object], None] | None,
    approval_callback: Any | None = None,
) -> Any:
    return execute_gateway_turn_impl(
        runtime=runtime,
        agent_name=agent_name,
        channel=channel,
        target=target,
        context=context,
        session_id=session_id,
        idempotency_key=idempotency_key,
        request_id=request_id,
        deliver=deliver,
        capability_category=capability_category,
        timeout_seconds=timeout_seconds,
        run_profile_overrides=run_profile_overrides,
        run_gateway_once=_run_gateway_once,
        progress_callback=progress_callback,
        approval_callback=approval_callback,
    )


async def _run_gateway_once(
    *,
    gateway: Any,
    channel: str,
    target: str,
    message: str,
    session_id: str | None,
    idempotency_key: str | None,
    request_id: str | None,
    inbound_metadata: dict[str, str] | None,
    deliver: bool,
    forced_tools: list[str] | None = None,
    capability_category: str | None = None,
    progress_callback: Callable[[object], None] | None = None,
    approval_callback: Any | None = None,
) -> Any:
    return await run_gateway_once_impl(
        gateway=gateway,
        channel=channel,
        target=target,
        message=message,
        session_id=session_id,
        idempotency_key=idempotency_key,
        request_id=request_id,
        inbound_metadata=inbound_metadata,
        deliver=deliver,
        forced_tools=forced_tools,
        capability_category=capability_category,
        progress_callback=progress_callback,
        approval_callback=approval_callback,
    )
