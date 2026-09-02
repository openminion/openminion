"""Runtime ingress facade and compatibility patch points."""

from typing import TYPE_CHECKING, Any, Callable

from openminion.base.config import combine_run_profile_overrides
from openminion.base.config.core import resolve_default_agent_id

from .execution import (
    _build_turn_context,
    execute_runtime_turn as _execute_runtime_turn_impl,
)
from .gateway_call import run_gateway_once_impl as _run_gateway_once
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
    TurnRequestError,
    TurnTimeoutError,
)

if TYPE_CHECKING:
    from openminion.services.runtime.interfaces import RuntimeFacade

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
    runtime: "RuntimeFacade",
    payload: dict[str, Any],
    request_id: str | None = None,
    progress_callback: Callable[[object], None] | None = None,
    approval_callback: Any | None = None,
    cancel_event: Any | None = None,
) -> dict[str, Any]:
    from openminion.modules.telemetry.trace.phase_timing import (
        ChatPhaseTimer,
        active_chat_phase,
        use_chat_phase_timer,
    )

    cold_start = bool(payload.get("__crtl_cold_start__", False))
    timer = ChatPhaseTimer(cold_start=cold_start)
    with use_chat_phase_timer(timer), active_chat_phase("provider_request_build"):
        request = runtime_turn_request_from_payload(
            runtime=runtime,
            payload=payload,
            request_id=request_id,
        )
    try:
        with use_chat_phase_timer(timer):
            result = execute_runtime_turn(
                runtime=runtime,
                request=request,
                progress_callback=progress_callback,
                approval_callback=approval_callback,
                cancel_event=cancel_event,
            )
            with active_chat_phase("response_normalization"):
                return result.as_payload()
    finally:
        _emit_chat_phase_timing(runtime=runtime, timer=timer, request=request)


def submit_turn_payload(
    *,
    runtime: "RuntimeFacade",
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
    return RuntimeTurnHandle(
        request=request, handle=manager.submit_turn(request), timeout_s=timeout_s
    )


def execute_runtime_turn(
    *,
    runtime: "RuntimeFacade",
    request: RuntimeTurnRequest,
    progress_callback: Callable[[object], None] | None = None,
    approval_callback: Any | None = None,
    cancel_event: Any | None = None,
) -> RuntimeTurnResult:
    return _execute_runtime_turn_impl(
        runtime=runtime,
        request=request,
        run_gateway_once=_run_gateway_once,
        progress_callback=progress_callback,
        approval_callback=approval_callback,
        cancel_event=cancel_event,
    )
