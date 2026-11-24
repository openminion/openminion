import asyncio
import concurrent.futures
import contextlib
import inspect
import logging
import threading
from collections.abc import AsyncIterator
from typing import Any, Callable, Dict, Optional, Tuple, TypedDict, cast
from uuid import UUID, uuid4

from openminion.base.channel import ChannelRegistry
from openminion.base.types import Message
from openminion.base.user_io import UserIO
from openminion.services.agent import AgentService
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
)
from openminion.modules.controlplane.channels.authenticity import (
    ChannelAuthenticityPolicy,
)
from openminion.modules.task.run import append_run_state_event
from openminion.services.context.session import SessionContextService
from openminion.services.gateway.config import (
    resolve_memory_capsule_strategy,
    resolve_memory_dynamic_retrieval_enabled,
)
from openminion.services.gateway.constants import CALLER_HANDLES_DELIVERY_METADATA_KEY
from openminion.services.gateway.routing import parse_metadata_bool
from openminion.services.gateway.security import GatewaySecurity
from openminion.services.gateway.streaming import (
    GatewayStreamEvent,
    gateway_stream_event_from_message,
    gateway_stream_event_from_turn_chunk,
)
from openminion.services.gateway.turn_intent import TypedTurnIntent
from openminion.services.gateway.turn import GatewayTurnRunner
from openminion.services.gateway.turn.route_classification import (
    classify_setup_cost_route,
)
from openminion.services.gateway.turn.runtime import (
    _await_with_progress_indicator,
    _interactive_user_prompt,
    _legacy_request_hash,
    _message_from_cache,
    _message_to_cache,
    _normalize_metadata,
    _request_hash,
)
from openminion.modules.policy import SecurityPolicyEngine
from openminion.modules.storage.runtime.idempotency_store import (
    IdempotencyRecord,
    IdempotencyStore,
)
from openminion.modules.storage.runtime.retrieval_service import RetrievalService
from openminion.modules.storage.runtime.session_store import (
    EventRecord,
    RuntimeSessionTurnFenceError,
    SessionStore,
)
from openminion.modules.telemetry.invocation_repair import InvocationLifecycleReconciler

_BRAIN_INTEGRATION_MODE_AUTHORITATIVE = "contextctl_authoritative"
_BRAIN_INTEGRATION_MODE_LEGACY_ALIAS = "ctxctl_authoritative"
_TERMINAL_RUN_EVENT_TYPES = frozenset({"run.completed", "run.failed", "run.cancelled"})
_USER_IO = UserIO()


class _IdempotencyRequestInputs(TypedDict):
    channel: str
    target: str
    body: str
    session_id: Optional[str]
    inbound_metadata: Optional[dict[str, str]]
    request_id: Optional[str]
    deliver: bool
    exclude_history_message_ids: tuple[str, ...]
    forced_tools: Optional[list[str]]
    capability_category: Optional[str]
    typed_turn_intent: TypedTurnIntent | None
    image_refs: tuple[str, ...]


def _is_generated_uuid4_hex(value: str) -> bool:
    if len(value) != 32 or value != value.lower():
        return False
    try:
        return UUID(hex=value).version == 4
    except ValueError:
        return False


def _run_gateway_stream_worker(
    handler: Callable[..., Any],
    kwargs: dict[str, Any],
    *,
    ready: threading.Event,
    stopped: threading.Event,
    state: dict[str, Any],
) -> Message:
    worker_loop = asyncio.new_event_loop()
    worker_task = worker_loop.create_task(handler(**kwargs))
    state.update(loop=worker_loop, task=worker_task)
    ready.set()
    try:
        return worker_loop.run_until_complete(worker_task)
    finally:
        stopped.set()
        with contextlib.suppress(Exception):
            worker_loop.run_until_complete(worker_loop.shutdown_asyncgens())
        with contextlib.suppress(Exception):
            worker_loop.run_until_complete(worker_loop.shutdown_default_executor())
        worker_loop.close()


async def _cancel_gateway_stream_worker(
    task: asyncio.Task[Message],
    *,
    ready: threading.Event,
    stopped: threading.Event,
    state: dict[str, Any],
) -> None:
    await asyncio.to_thread(ready.wait, 1.0)
    worker_loop = state.get("loop")
    worker_task = state.get("task")
    if worker_loop is not None and worker_task is not None:
        with contextlib.suppress(RuntimeError):
            worker_loop.call_soon_threadsafe(worker_task.cancel)
    await asyncio.to_thread(stopped.wait, 5.0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


class GatewayService:
    _METHOD_HANDLE_MESSAGE = "gateway.handle_message"

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def __init__(
        self,
        agent: AgentService,
        channels: ChannelRegistry,
        logger: logging.Logger,
        sessions: SessionStore,
        idempotency: IdempotencyStore,
        agent_id: str,
        security_policy: SecurityPolicyEngine | None = None,
        channel_authenticity_policy: ChannelAuthenticityPolicy | None = None,
        history_limit: int = 20,
        session_context: SessionContextService | None = None,
        agent_memory: object | None = None,
        owns_agent_memory: bool = False,
        knowledge_graphs: object | None = None,
        brain_integration_mode: str = _BRAIN_INTEGRATION_MODE_AUTHORITATIVE,
        retrieval_service: RetrievalService | None = None,
        contextctl_adapter: Any | None = None,
        context_token_budget: int = 0,
        record_context_selection: Callable[[str], None] | None = None,
    ) -> None:
        self._agent = agent
        self._channels = channels
        self._logger = logger
        self._sessions = sessions
        self._idempotency = idempotency
        self._agent_id = agent_id
        self._security_policy = security_policy
        self._channel_authenticity_policy = channel_authenticity_policy
        self._history_limit = max(1, history_limit)
        self._session_context = session_context or SessionContextService(
            sessions,
            logger=logger.getChild("session_context"),
            keep_recent_messages=self._history_limit,
        )
        self._agent_memory: Any = agent_memory or DisabledMemoryGatewayAdapter(
            agent_id=self._agent_id, logger=logger.getChild("agent_memory")
        )
        self._owns_agent_memory = owns_agent_memory
        self._knowledge_graphs = knowledge_graphs
        self._retrieval_service = retrieval_service
        if contextctl_adapter is None:
            from openminion.modules.context.memory_client import (
                ContextMemoryClientAdapter,
            )
            from openminion.modules.context.slices import RuntimeMappedSessionClient
            from openminion.services.context.adapter import ContextCtlGatewayAdapter

            contextctl_adapter = ContextCtlGatewayAdapter.from_env(
                agent_id=agent_id,
                runtime_token_budget=context_token_budget,
                session_client=RuntimeMappedSessionClient(store=self._sessions),
                memory_client=ContextMemoryClientAdapter(self._agent_memory),
                record_context_selection=record_context_selection,
                logger=logger.getChild("contextctl"),
            )
        self._contextctl_adapter = contextctl_adapter
        normalized_mode = str(brain_integration_mode or "").strip().lower()
        if normalized_mode == _BRAIN_INTEGRATION_MODE_LEGACY_ALIAS:
            normalized_mode = _BRAIN_INTEGRATION_MODE_AUTHORITATIVE
        if normalized_mode != _BRAIN_INTEGRATION_MODE_AUTHORITATIVE:
            raise RuntimeError(
                "Legacy gateway integration mode is disabled. "
                "Set gateway.brain_integration_mode=contextctl_authoritative."
            )
        self._brain_integration_mode = normalized_mode
        self._inflight: Dict[Tuple[str, str], asyncio.Task[Message]] = {}
        self._memory_capsule_strategy = resolve_memory_capsule_strategy(agent)
        self._memory_capsule_cache: Dict[str, str] = {}
        self._memory_dynamic_retrieval_enabled = (
            resolve_memory_dynamic_retrieval_enabled(agent)
        )
        self._security = GatewaySecurity(
            sessions=self._sessions,
            logger=self._logger.getChild("security"),
            agent_id=self._agent_id,
            security_policy=self._security_policy,
            channel_authenticity_policy=self._channel_authenticity_policy,
        )
        self._turn_runner = GatewayTurnRunner(
            agent=self._agent,
            agent_memory=self._agent_memory,
            knowledge_graphs=self._knowledge_graphs,
            channels=self._channels,
            logger=self._logger,
            sessions=self._sessions,
            session_context=self._session_context,
            security=self._security,
            agent_id=self._agent_id,
            history_limit=self._history_limit,
            memory_capsule_strategy=self._memory_capsule_strategy,
            memory_capsule_cache=self._memory_capsule_cache,
            memory_dynamic_retrieval_enabled=self._memory_dynamic_retrieval_enabled,
            contextctl_adapter=self._contextctl_adapter,
            emit_run_state=self._emit_run_state,
            emit_invocation_lifecycle=getattr(
                self._agent, "emit_invocation_lifecycle_sync", None
            ),
        )

    def flush_memory_followups(self, *, session_id: str | None = None) -> None:
        self._turn_runner.flush_memory_followups(session_id=session_id)

    def release_session(self, session_id: str) -> None:
        self._turn_runner.release_session(session_id)
        self._contextctl_adapter.release_session(session_id)
        release_agent_session = getattr(self._agent, "release_session", None)
        if callable(release_agent_session):
            release_agent_session(session_id)

    def close_session(self, session_id: str, *, reason: str) -> None:
        self._session_context.on_session_close(session_id=session_id)
        self._sessions.close_session(session_id=session_id, reason=reason)
        self.release_session(session_id)

    def close(self) -> None:
        self.flush_memory_followups()
        self._memory_capsule_cache.clear()
        self._contextctl_adapter.close()
        close_memory = getattr(self._agent_memory, "close", None)
        if self._owns_agent_memory and callable(close_memory):
            close_memory()

    def repair_invocation_lifecycle(self, *, session_id: str) -> dict[str, object]:
        report = InvocationLifecycleReconciler.for_runtime(
            sessions=self._sessions,
            telemetryctl=self._agent.telemetry_contract,
        ).repair_session(session_id)
        return report.to_dict()

    async def handle_message(
        self,
        channel: str,
        target: str,
        body: str,
        session_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        request_id: Optional[str] = None,
        inbound_metadata: Optional[dict[str, str]] = None,
        deliver: bool = True,
        exclude_history_message_ids: tuple[str, ...] = (),
        forced_tools: Optional[list[str]] = None,
        capability_category: Optional[str] = None,
        typed_turn_intent: TypedTurnIntent | None = None,
        image_refs: tuple[str, ...] = (),
        progress_callback: Callable[[object], None] | None = None,
        approval_callback: Callable[..., Any] | None = None,
    ) -> Message:
        dedupe_key = (idempotency_key or "").strip()
        if dedupe_key:
            return await self._handle_message_with_idempotency(
                channel=channel,
                target=target,
                body=body,
                session_id=session_id,
                idempotency_key=dedupe_key,
                request_id=request_id,
                inbound_metadata=inbound_metadata,
                deliver=deliver,
                exclude_history_message_ids=exclude_history_message_ids,
                forced_tools=forced_tools,
                capability_category=capability_category,
                typed_turn_intent=typed_turn_intent,
                image_refs=image_refs,
                progress_callback=progress_callback,
                approval_callback=approval_callback,
            )
        return await self._handle_message_once(
            channel=channel,
            target=target,
            body=body,
            session_id=session_id,
            request_id=request_id,
            inbound_metadata=inbound_metadata,
            deliver=deliver,
            exclude_history_message_ids=exclude_history_message_ids,
            forced_tools=forced_tools,
            capability_category=capability_category,
            typed_turn_intent=typed_turn_intent,
            image_refs=image_refs,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
        )

    async def handle_message_streaming(
        self,
        channel: str,
        target: str,
        body: str,
        session_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        request_id: Optional[str] = None,
        inbound_metadata: Optional[dict[str, str]] = None,
        deliver: bool = True,
        forced_tools: Optional[list[str]] = None,
        capability_category: Optional[str] = None,
        typed_turn_intent: TypedTurnIntent | None = None,
        image_refs: tuple[str, ...] = (),
        approval_callback: Callable[..., Any] | None = None,
    ) -> AsyncIterator[GatewayStreamEvent]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[GatewayStreamEvent] = asyncio.Queue()

        def _progress_callback(payload: object) -> None:
            if not isinstance(payload, dict):
                if hasattr(payload, "model_dump"):
                    try:
                        payload = payload.model_dump()
                    except Exception:
                        return
                else:
                    return
            event = gateway_stream_event_from_turn_chunk(
                dict(cast(dict[Any, Any], payload))
            )
            if event is None:
                return
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except Exception:
                return

        def _approval_callback_bridge(
            callback: Callable[..., Any] | None,
        ) -> Callable[..., Any] | None:
            if callback is None:
                return None

            async def _approval_callback(*args: Any, **kwargs: Any) -> Any:
                future: concurrent.futures.Future[Any] = concurrent.futures.Future()

                def _invoke_on_stream_loop() -> None:
                    try:
                        result = callback(*args, **kwargs)
                        if inspect.isawaitable(result):

                            async def _await_result() -> None:
                                try:
                                    future.set_result(await result)
                                except Exception as exc:  # pragma: no cover - bridge
                                    future.set_exception(exc)

                            asyncio.create_task(_await_result())
                        else:
                            future.set_result(result)
                    except Exception as exc:  # pragma: no cover - bridge
                        future.set_exception(exc)

                loop.call_soon_threadsafe(_invoke_on_stream_loop)
                return await asyncio.to_thread(future.result)

            return _approval_callback

        bridged_approval_callback = _approval_callback_bridge(approval_callback)
        worker_ready, worker_stopped = threading.Event(), threading.Event()
        worker_state: dict[str, Any] = {}

        worker_kwargs = {
            "channel": channel,
            "target": target,
            "body": body,
            "session_id": session_id,
            "idempotency_key": idempotency_key,
            "request_id": request_id,
            "inbound_metadata": inbound_metadata,
            "deliver": deliver,
            "forced_tools": forced_tools,
            "capability_category": capability_category,
            "typed_turn_intent": typed_turn_intent,
            "image_refs": image_refs,
            "progress_callback": _progress_callback,
            "approval_callback": bridged_approval_callback,
        }
        task = asyncio.create_task(
            asyncio.to_thread(
                _run_gateway_stream_worker,
                self.handle_message,
                worker_kwargs,
                ready=worker_ready,
                stopped=worker_stopped,
                state=worker_state,
            )
        )
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue
                yield event
            message = await task
            yield gateway_stream_event_from_message(message)
        finally:
            if not task.done():
                await _cancel_gateway_stream_worker(
                    task,
                    ready=worker_ready,
                    stopped=worker_stopped,
                    state=worker_state,
                )

    async def _handle_message_with_idempotency(
        self,
        *,
        channel: str,
        target: str,
        body: str,
        session_id: Optional[str],
        idempotency_key: str,
        request_id: Optional[str],
        inbound_metadata: Optional[dict[str, str]],
        deliver: bool,
        exclude_history_message_ids: tuple[str, ...] = (),
        forced_tools: Optional[list[str]] = None,
        capability_category: Optional[str] = None,
        typed_turn_intent: TypedTurnIntent | None = None,
        image_refs: tuple[str, ...] = (),
        progress_callback: Callable[[object], None] | None = None,
        approval_callback: Callable[..., Any] | None = None,
    ) -> Message:
        request_inputs: _IdempotencyRequestInputs = {
            "channel": channel,
            "target": target,
            "body": body,
            "session_id": session_id,
            "inbound_metadata": inbound_metadata,
            "request_id": request_id,
            "deliver": deliver,
            "exclude_history_message_ids": exclude_history_message_ids,
            "forced_tools": forced_tools,
            "capability_category": capability_category,
            "typed_turn_intent": typed_turn_intent,
            "image_refs": image_refs,
        }
        request_hash = _request_hash(agent_id=self._agent_id, **request_inputs)
        cached = self._completed_idempotent_message(
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_inputs=request_inputs,
        )
        if cached is not None:
            return cached
        inflight_key = (self._METHOD_HANDLE_MESSAGE, idempotency_key)
        inflight = self._inflight.get(inflight_key)
        if inflight is not None:
            return await inflight
        reserved = self._idempotency.reserve(
            method=self._METHOD_HANDLE_MESSAGE,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if not reserved:
            cached = self._completed_idempotent_message(
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                request_inputs=request_inputs,
            )
            if cached is not None:
                return cached
            inflight = self._inflight.get(inflight_key)
            if inflight is not None:
                return await inflight

        task = asyncio.create_task(
            self._handle_message_once(
                **request_inputs,
                progress_callback=progress_callback,
                approval_callback=approval_callback,
            )
        )
        self._inflight[inflight_key] = task
        try:
            result = await task
            self._idempotency.complete(
                method=self._METHOD_HANDLE_MESSAGE,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response=_message_to_cache(result),
                status="completed",
            )
            return result
        except Exception as exc:
            self._idempotency.complete(
                method=self._METHOD_HANDLE_MESSAGE,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                response={"error": str(exc)},
                status="failed",
            )
            raise
        finally:
            self._inflight.pop(inflight_key, None)

    def _completed_idempotent_message(
        self,
        *,
        idempotency_key: str,
        request_hash: str,
        request_inputs: _IdempotencyRequestInputs,
    ) -> Message | None:
        existing = self._idempotency.get(
            method=self._METHOD_HANDLE_MESSAGE,
            idempotency_key=idempotency_key,
        )
        if existing is None:
            return None
        if existing.request_hash == request_hash:
            return (
                _message_from_cache(existing.response)
                if existing.status == "completed"
                else None
            )
        legacy_message = self._recover_legacy_completed_message(
            existing=existing,
            request_inputs=request_inputs,
        )
        if legacy_message is not None:
            return legacy_message
        raise ValueError("idempotency key conflicts with an earlier request")

    def _recover_legacy_completed_message(
        self,
        *,
        existing: IdempotencyRecord,
        request_inputs: _IdempotencyRequestInputs,
    ) -> Message | None:
        if (
            existing.status != "completed"
            or request_inputs["exclude_history_message_ids"]
            or request_inputs["forced_tools"]
            or request_inputs["capability_category"]
        ):
            return None
        legacy_hash = _legacy_request_hash(
            channel=request_inputs["channel"],
            target=request_inputs["target"],
            body=request_inputs["body"],
            session_id=request_inputs["session_id"],
            inbound_metadata=request_inputs["inbound_metadata"],
            typed_turn_intent=request_inputs["typed_turn_intent"],
            image_refs=request_inputs["image_refs"],
        )
        if existing.request_hash != legacy_hash:
            return None
        cached = _message_from_cache(existing.response)
        meta = cached.metadata
        if (
            meta.get("replayed_response", "").strip().lower() == "true"
            or meta.get("suppressed", "").strip()
        ):
            return None
        cached_session_id = meta.get("session_id", "").strip()
        cached_run_id = meta.get("run_id", "").strip()
        cached_request_id = meta.get("request_id", "").strip()
        cached_persisted_id = meta.get("persisted_outbound_message_id", "").strip()
        if not (
            cached_session_id
            and cached_run_id
            and cached_request_id
            and cached_persisted_id
        ):
            return None
        requested_session_id = (request_inputs["session_id"] or "").strip()
        if requested_session_id and cached_session_id != requested_session_id:
            return None
        supplied_request_id = request_inputs["request_id"] or ""
        if supplied_request_id:
            if cached_request_id != supplied_request_id:
                return None
        elif not _is_generated_uuid4_hex(cached_request_id):
            return None
        recorded_category = (
            meta.get("capability_category") or meta.get("intent_category") or ""
        )
        if recorded_category and recorded_category != "none":
            return None
        if not request_inputs["body"].strip().lower().startswith("tool ") and (
            meta.get("tool_loop_termination_reason") == "forced_tool_unavailable"
            or (
                "capability_attempted_tools" in meta
                and meta["capability_attempted_tools"] != "[]"
            )
        ):
            return None
        return (
            cached
            if self._legacy_turn_events_match(
                session_id=cached_session_id,
                run_id=cached_run_id,
                request_id=cached_request_id,
                persisted_outbound_id=cached_persisted_id,
                request_inputs=request_inputs,
            )
            else None
        )

    def _legacy_turn_events_match(
        self,
        *,
        session_id: str,
        run_id: str,
        request_id: str,
        persisted_outbound_id: str,
        request_inputs: _IdempotencyRequestInputs,
    ) -> bool:
        queued_event: EventRecord | None = None
        running_event: EventRecord | None = None
        terminal_event: EventRecord | None = None
        for event in reversed(
            self._sessions.list_run_events(
                session_id=session_id, run_id=run_id, limit=100
            )
        ):
            if event.event_type == "run.queued" and queued_event is None:
                queued_event = event
            elif event.event_type == "run.running" and running_event is None:
                running_event = event
            elif event.event_type in _TERMINAL_RUN_EVENT_TYPES:
                terminal_event = event
        if (
            queued_event is None
            or running_event is None
            or terminal_event is None
            or terminal_event.id <= queued_event.id
            or str(queued_event.payload.get("agent_id", "")).strip() != self._agent_id
            or str(queued_event.payload.get("request_id", "")).strip() != request_id
            or str(queued_event.payload.get("channel", "")) != request_inputs["channel"]
            or str(queued_event.payload.get("target", "")) != request_inputs["target"]
        ):
            return False
        expected_route = classify_setup_cost_route(
            message=request_inputs["body"],
            forced_tools=None,
            capability_category=None,
            inbound_metadata=request_inputs["inbound_metadata"],
        )
        if (
            str(running_event.payload.get("setup_cost_route", "")).strip()
            != expected_route.label
            or str(running_event.payload.get("setup_cost_route_reason", "")).strip()
            != expected_route.reason
        ):
            return False
        persisted_matched = False
        delivery_modes: set[str] = set()
        cursor, end_id = queued_event.id, terminal_event.id - 1
        while cursor < end_id:
            batch = self._sessions.list_events_after_id(
                session_id=session_id, after_id=cursor, high_water_id=end_id
            )
            if not batch:
                break
            for event in batch:
                payload = event.payload
                if (
                    str(payload.get("run_id", "")).strip() != run_id
                    or str(payload.get("response_id", "")).strip()
                    != persisted_outbound_id
                ):
                    continue
                if event.event_type == "response.persisted":
                    persisted_matched = True
                elif (
                    event.event_type == "response.delivered"
                    and str(payload.get("thread_decision_action", "")).strip()
                    != "replay"
                ):
                    mode = str(payload.get("delivery_mode", "")).strip()
                    if mode:
                        delivery_modes.add(mode)
            cursor = batch[-1].id
        if not persisted_matched:
            return False
        if request_inputs["deliver"]:
            return "channel" in delivery_modes
        if "channel" in delivery_modes:
            return False
        caller_handles_delivery = parse_metadata_bool(
            _normalize_metadata(request_inputs["inbound_metadata"]),
            CALLER_HANDLES_DELIVERY_METADATA_KEY,
        )
        return (
            "return" in delivery_modes
            if caller_handles_delivery
            else not delivery_modes
        )

    async def _handle_message_once(
        self,
        *,
        channel: str,
        target: str,
        body: str,
        session_id: Optional[str],
        request_id: Optional[str],
        inbound_metadata: Optional[dict[str, str]],
        deliver: bool,
        exclude_history_message_ids: tuple[str, ...] = (),
        forced_tools: Optional[list[str]] = None,
        capability_category: Optional[str] = None,
        typed_turn_intent: TypedTurnIntent | None = None,
        image_refs: tuple[str, ...] = (),
        progress_callback: Callable[[object], None] | None = None,
        approval_callback: Callable[..., Any] | None = None,
    ) -> Message:
        request_value = str(request_id or "").strip() or uuid4().hex
        resolved_session = self._sessions.resolve_session(
            agent_id=self._agent_id,
            channel=channel,
            target=target,
            session_id=session_id,
            resume_existing=False,
        )
        session_value = resolved_session.id
        lease = self._sessions.acquire_session_turn_lease(
            session_value,
            owner=f"gateway:{request_value}",
            request_id=request_value,
            ttl_s=60,
        )
        try:
            session_metadata = dict(inbound_metadata or {})
            session_metadata.pop("openminion_ephemeral_workspace_roots", None)
            if session_metadata:
                resolved_session = self._sessions.update_session_metadata(
                    session_id=session_value,
                    patch=session_metadata,
                    session_turn_fence_token=lease.fence_token,
                )
            if str(session_id or "").strip():
                resolved_session = self._sessions.resume_session(
                    session_id=session_value,
                    session_turn_fence_token=lease.fence_token,
                )
            return await self._turn_runner.run(
                channel=channel,
                target=target,
                body=body,
                session_id=session_value,
                request_id=request_value,
                inbound_metadata=inbound_metadata,
                deliver=deliver,
                exclude_history_message_ids=exclude_history_message_ids,
                forced_tools=forced_tools,
                capability_category=capability_category,
                typed_turn_intent=typed_turn_intent,
                image_refs=image_refs,
                progress_callback=progress_callback,
                approval_callback=approval_callback,
                resolved_session=resolved_session,
                session_turn_lease_owner=lease.owner,
                session_turn_fence_token=lease.fence_token,
            )
        finally:
            self._sessions.release_session_turn_lease(
                session_value,
                owner=lease.owner,
                fence_token=lease.fence_token,
            )

    async def run_once(
        self,
        channel: str,
        target: str,
        message: str,
        session_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        request_id: Optional[str] = None,
        inbound_metadata: Optional[dict[str, str]] = None,
        deliver: bool = True,
        exclude_history_message_ids: tuple[str, ...] = (),
        forced_tools: Optional[list[str]] = None,
        capability_category: Optional[str] = None,
        typed_turn_intent: TypedTurnIntent | None = None,
        image_refs: tuple[str, ...] = (),
        progress_callback: Callable[[object], None] | None = None,
        approval_callback: Callable[..., Any] | None = None,
    ) -> Message:
        self._logger.info("gateway single turn channel=%s target=%s", channel, target)
        return await self.handle_message(
            channel=channel,
            target=target,
            body=message,
            session_id=session_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            inbound_metadata=inbound_metadata,
            deliver=deliver,
            exclude_history_message_ids=exclude_history_message_ids,
            forced_tools=forced_tools,
            capability_category=capability_category,
            typed_turn_intent=typed_turn_intent,
            image_refs=image_refs,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
        )

    async def run_loop(
        self,
        channel: str,
        target: Optional[str] = None,
        show_progress: bool = True,
    ) -> None:
        loop_target = target or "local-user"
        attach_id = f"att-{uuid4().hex}"
        self._logger.info(
            "gateway interactive loop started channel=%s target=%s",
            channel,
            loop_target,
        )
        _USER_IO.out("OpenMinion gateway loop. Type 'exit' or 'quit' to stop.")
        while True:
            try:
                text = input(_interactive_user_prompt()).strip()
            except (EOFError, KeyboardInterrupt):
                _USER_IO.blank()
                break

            if not text:
                continue
            if text.lower() in {"exit", "quit"}:
                break

            pending = asyncio.create_task(
                self.handle_message(
                    channel=channel,
                    target=loop_target,
                    body=text,
                    deliver=True,
                    inbound_metadata={"attach_id": attach_id},
                )
            )
            try:
                if show_progress:
                    await _await_with_progress_indicator(pending, label="openminion")
                await pending
            finally:
                if not pending.done():
                    pending.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pending

        self._logger.info("gateway interactive loop stopped")

    def _emit_run_state(
        self,
        *,
        session_id: str,
        run_id: str,
        state: str,
        current_step: str,
        payload: Optional[dict[str, Any]] = None,
        session_turn_fence_token: int | None = None,
    ) -> EventRecord | None:
        try:
            return append_run_state_event(
                self._sessions,
                session_id=session_id,
                run_id=run_id,
                state=state,
                current_step=current_step,
                payload=payload,
                session_turn_fence_token=session_turn_fence_token,
            )
        except RuntimeSessionTurnFenceError:
            raise
        except Exception as exc:
            self._logger.warning(
                "failed to append run event session_id=%s run_id=%s state=%s error=%s",
                session_id,
                run_id,
                state,
                exc,
            )
            return None
