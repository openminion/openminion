"""Execution routing for runtime ingress."""

import asyncio
from types import MappingProxyType
from typing import Any, Awaitable, Callable
from uuid import uuid4

from openminion.base.config import RunProfileOverrides
from openminion.modules.controlplane.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)
from openminion.modules.telemetry.usage import RunStats
from openminion.modules.runtime.sync import await_with_cancel
from openminion.services.runtime.turn_router import TurnRouter

from .gateway_call import _run_coro_sync
from .payloads import mutable_inbound_metadata as _mutable_inbound_metadata
from .types import (
    RuntimeTurnRequest,
    RuntimeTurnResult,
    TurnContext,
    TurnRequestError,
    TurnTimeoutError,
    freeze_metadata,
)

RunGatewayOnce = Callable[..., Awaitable[Any]]


def execute_runtime_turn(
    *,
    runtime: Any,
    request: RuntimeTurnRequest,
    run_gateway_once: RunGatewayOnce,
    progress_callback: Callable[[object], None] | None = None,
    approval_callback: Any | None = None,
    cancel_event: Any | None = None,
) -> RuntimeTurnResult:
    context = _build_turn_context(
        message=request.message,
        forced_tools=list(request.forced_tools),
        inbound_metadata=dict(request.inbound_metadata or {}) or None,
    )
    routed_agents, routing_mode = _routed_agents(runtime=runtime, request=request)
    if len(routed_agents) == 1:
        result = execute_gateway_turn_impl(
            runtime=runtime,
            agent_name=routed_agents[0],
            channel=request.channel,
            target=request.target,
            context=context,
            session_id=request.session_id,
            idempotency_key=request.idempotency_key,
            request_id=request.request_id,
            exclude_history_message_ids=(),
            deliver=request.deliver,
            capability_category=request.capability_category,
            timeout_seconds=request.timeout_seconds,
            run_profile_overrides=request.run_profile_overrides,
            run_gateway_once=run_gateway_once,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
            cancel_event=cancel_event,
        )
    else:
        result = _execute_routed_turns(
            runtime=runtime,
            request=request,
            context=context,
            routed_agents=routed_agents,
            routing_mode=routing_mode,
            run_gateway_once=run_gateway_once,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
            cancel_event=cancel_event,
        )
    metadata = dict(getattr(result, "metadata", {}) or {})
    response_agent_id = (
        routed_agents[0] if len(routed_agents) == 1 else request.agent_id
    )
    return RuntimeTurnResult(
        id=str(getattr(result, "id", "")).strip(),
        channel=str(getattr(result, "channel", request.channel)).strip(),
        target=str(getattr(result, "target", request.target)).strip(),
        body=str(getattr(result, "body", "")).strip(),
        metadata=MappingProxyType(metadata),
        agent_id=response_agent_id,
        stats=getattr(result, "stats", None),
    )


def _routed_agents(
    *, runtime: Any, request: RuntimeTurnRequest
) -> tuple[tuple[str, ...], str]:
    session, participants = None, []
    if request.session_id:
        session = runtime.sessions.get_session(request.session_id)
        if session is not None:
            participants = runtime.sessions.list_participants(request.session_id)
    decision = TurnRouter().route(
        session=session,
        message=request.message,
        participants=participants,
        requested_agent_id=request.profile_agent_id,
        configured_agent_ids=runtime.config.agents,
    )
    routed_agents = tuple(agent_id for agent_id in decision.agent_ids if agent_id)
    return routed_agents or (request.profile_agent_id,), decision.mode


def _execute_routed_turns(
    *,
    runtime: Any,
    request: RuntimeTurnRequest,
    context: TurnContext,
    routed_agents: tuple[str, ...],
    routing_mode: str,
    run_gateway_once: RunGatewayOnce,
    progress_callback: Callable[[object], None] | None,
    approval_callback: Any | None,
    cancel_event: Any | None,
) -> Any:
    if request.deliver:
        raise TurnRequestError("Multi-agent room turns require deliver=False.")

    routed_results = []
    request_id_base = request.request_id or uuid4().hex
    persisted_inbound_message_id = ""
    persisted_outbound_message_ids: list[str] = []
    for index, agent_name in enumerate(routed_agents):
        inbound_metadata = dict(request.inbound_metadata or {})
        inbound_metadata[CALLER_HANDLES_DELIVERY_METADATA_KEY] = "true"
        exclude_history_message_ids: tuple[str, ...] = ()
        if index > 0:
            inbound_metadata["room_router_skip_inbound_persist"] = "true"
            inbound_metadata["room_router_skip_session_compaction"] = "true"
            inbound_metadata["persisted_inbound_message_id"] = (
                persisted_inbound_message_id
            )
            prior_outputs = (
                persisted_outbound_message_ids if routing_mode == "broadcast" else []
            )
            exclude_history_message_ids = (persisted_inbound_message_id, *prior_outputs)
        result = execute_gateway_turn_impl(
            runtime=runtime,
            agent_name=agent_name,
            channel=request.channel,
            target=request.target,
            context=_build_turn_context(
                message=request.message,
                forced_tools=list(context.forced_tools),
                inbound_metadata=inbound_metadata or None,
            ),
            session_id=request.session_id,
            idempotency_key=(
                f"{request.idempotency_key}::{agent_name}"
                if request.idempotency_key
                else None
            ),
            request_id=f"{request_id_base}::{agent_name}",
            exclude_history_message_ids=exclude_history_message_ids,
            deliver=False,
            capability_category=request.capability_category,
            timeout_seconds=request.timeout_seconds,
            run_profile_overrides=request.run_profile_overrides,
            run_gateway_once=run_gateway_once,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
            cancel_event=cancel_event,
        )
        routed_results.append((agent_name, result))
        result_metadata = result.metadata
        if index == 0:
            persisted_inbound_message_id = str(
                result_metadata.get("persisted_inbound_message_id", "")
            ).strip()
        persisted_outbound_message_ids.append(
            str(result_metadata.get("persisted_outbound_message_id", "")).strip()
        )
    return _aggregate_routed_results(
        routed_results=routed_results,
        routing_mode=routing_mode,
    )


def _aggregate_routed_results(
    *,
    routed_results: list[tuple[str, Any]],
    routing_mode: str,
) -> Any:
    if len(routed_results) == 1:
        return routed_results[0][1]

    parts: list[str] = []
    aggregate_metadata: dict[str, Any] = {}
    last_result = routed_results[-1][1]
    last_metadata = last_result.metadata
    for key in ("session_id", "conversation_id", "thread_id", "attach_id", "run_id"):
        value = last_metadata.get(key)
        if value is not None and str(value).strip():
            aggregate_metadata[key] = value
    room_responses = []
    for agent_id, result in routed_results:
        body = str(result.body).strip()
        metadata = result.metadata
        room_responses.append(
            {
                "agent_id": agent_id,
                "body": body,
                "persisted_outbound_message_id": str(
                    metadata.get("persisted_outbound_message_id", "")
                ).strip(),
            }
        )
        if body:
            parts.append(f"[{agent_id}]\n{body}")
    aggregate_metadata["room_responses"] = room_responses
    aggregate_metadata["room_routing_mode"] = routing_mode
    aggregate_metadata["room_routed_agents"] = ",".join(
        agent_id for agent_id, _ in routed_results
    )
    aggregate_metadata["room_aggregated"] = "true"
    aggregate_stats: RunStats | None = None
    for _, result in routed_results:
        result_stats = result.stats
        if result_stats is None:
            continue
        aggregate_stats = (
            result_stats
            if aggregate_stats is None
            else aggregate_stats.add(result_stats)
        )

    return RuntimeTurnResult(
        id=str(last_result.id).strip(),
        channel=str(last_result.channel).strip(),
        target=str(last_result.target).strip(),
        body="\n\n".join(parts).strip(),
        metadata=MappingProxyType(aggregate_metadata),
        agent_id=str(routed_results[0][0]),
        stats=aggregate_stats,
    )


def _build_turn_context(
    *,
    message: str,
    forced_tools: list[str] | None,
    inbound_metadata: dict[str, str] | None,
) -> TurnContext:
    return TurnContext(
        message=message,
        forced_tools=tuple(forced_tools or ()),
        inbound_metadata=freeze_metadata(inbound_metadata),
    )


def execute_gateway_turn_impl(
    *,
    runtime: Any,
    agent_name: str,
    channel: str,
    target: str,
    context: TurnContext,
    session_id: str | None,
    idempotency_key: str | None,
    request_id: str | None,
    exclude_history_message_ids: tuple[str, ...],
    deliver: bool,
    capability_category: str | None,
    timeout_seconds: float,
    run_profile_overrides: RunProfileOverrides,
    run_gateway_once: RunGatewayOnce,
    progress_callback: Callable[[object], None] | None,
    approval_callback: Any | None = None,
    cancel_event: Any | None = None,
) -> Any:
    try:
        return _run_coro_sync(
            lambda: await_with_cancel(
                run_gateway_once(
                    gateway=runtime.resolve_gateway(
                        agent_name,
                        overrides=run_profile_overrides,
                    ),
                    channel=channel,
                    target=target,
                    message=context.message,
                    session_id=session_id,
                    idempotency_key=idempotency_key,
                    request_id=request_id,
                    exclude_history_message_ids=exclude_history_message_ids,
                    inbound_metadata=_mutable_inbound_metadata(
                        context.inbound_metadata
                    ),
                    deliver=deliver,
                    forced_tools=list(context.forced_tools),
                    capability_category=capability_category,
                    progress_callback=progress_callback,
                    approval_callback=approval_callback,
                ),
                timeout_s=float(timeout_seconds),
                cancel_event=cancel_event,
            ),
            timeout=float(timeout_seconds),
        )
    except asyncio.TimeoutError as exc:
        raise TurnTimeoutError(
            f"Turn execution timed out after {timeout_seconds:.2f} seconds."
        ) from exc
