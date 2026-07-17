import asyncio
import concurrent.futures
import contextlib
import inspect
import logging
from collections.abc import AsyncIterator
from typing import Any, Callable, Dict, Optional, Tuple, cast
from uuid import uuid4

from openminion.base.channel import ChannelRegistry
from openminion.base.types import Message
from openminion.base.user_io import UserIO
from openminion.services.agent import AgentService
from openminion.modules.controlplane.channels.authenticity import ChannelAuthenticityPolicy
from openminion.modules.task.run import append_run_state_event
from openminion.services.context.session import SessionContextService
from openminion.services.gateway.config import (
    resolve_memory_capsule_strategy,
    resolve_memory_dynamic_retrieval_enabled,
)
from openminion.services.gateway.security import GatewaySecurity
from openminion.services.gateway.streaming import (
    GatewayStreamEvent,
    gateway_stream_event_from_message,
    gateway_stream_event_from_progress,
)
from openminion.services.gateway.turn_intent import TypedTurnIntent
from openminion.services.gateway.turn import GatewayTurnRunner
from openminion.services.gateway.turn.runtime import (
    _await_with_progress_indicator,
    _interactive_user_prompt,
    _message_from_cache,
    _message_to_cache,
    _request_hash,
)
from openminion.modules.policy import SecurityPolicyEngine
from openminion.modules.storage.runtime.idempotency_store import IdempotencyStore
from openminion.modules.storage.runtime.retrieval_service import RetrievalService
from openminion.modules.storage.runtime.session_store import SessionStore

_BRAIN_INTEGRATION_MODE_AUTHORITATIVE = "contextctl_authoritative"
_BRAIN_INTEGRATION_MODE_LEGACY_ALIAS = "ctxctl_authoritative"
_USER_IO = UserIO()


class GatewayService:
    _METHOD_HANDLE_MESSAGE = "gateway.handle_message"

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
        knowledge_graphs: object | None = None,
        brain_integration_mode: str = _BRAIN_INTEGRATION_MODE_AUTHORITATIVE,
        retrieval_service: RetrievalService | None = None,
    ) -> None:
        from openminion.services.agent.memory.gateway_adapter import (
            DisabledMemoryGatewayAdapter,
        )

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
        self._agent_memory = agent_memory or DisabledMemoryGatewayAdapter(
            agent_id=self._agent_id,
            logger=logger.getChild("agent_memory"),
        )
        self._knowledge_graphs = knowledge_graphs
        self._retrieval_service = retrieval_service
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
            emit_run_state=self._emit_run_state,
        )

    def flush_memory_followups(self, *, session_id: str | None = None) -> None:
        self._turn_runner.flush_memory_followups(session_id=session_id)

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
        forced_tools: Optional[list[str]] = None,
        capability_category: Optional[str] = None,
        typed_turn_intent: TypedTurnIntent | None = None,
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
                forced_tools=forced_tools,
                capability_category=capability_category,
                typed_turn_intent=typed_turn_intent,
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
            forced_tools=forced_tools,
            capability_category=capability_category,
            typed_turn_intent=typed_turn_intent,
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
            event = gateway_stream_event_from_progress(
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

        def _run_message_turn() -> Message:
            worker_loop = asyncio.new_event_loop()
            try:
                return worker_loop.run_until_complete(
                    self.handle_message(
                        channel=channel,
                        target=target,
                        body=body,
                        session_id=session_id,
                        idempotency_key=idempotency_key,
                        request_id=request_id,
                        inbound_metadata=inbound_metadata,
                        deliver=deliver,
                        forced_tools=forced_tools,
                        capability_category=capability_category,
                        typed_turn_intent=typed_turn_intent,
                        progress_callback=_progress_callback,
                        approval_callback=bridged_approval_callback,
                    )
                )
            finally:
                with contextlib.suppress(Exception):
                    worker_loop.run_until_complete(worker_loop.shutdown_asyncgens())
                with contextlib.suppress(Exception):
                    worker_loop.run_until_complete(
                        worker_loop.shutdown_default_executor()
                    )
                worker_loop.close()

        task = asyncio.create_task(asyncio.to_thread(_run_message_turn))
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
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

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
        forced_tools: Optional[list[str]] = None,
        capability_category: Optional[str] = None,
        typed_turn_intent: TypedTurnIntent | None = None,
        progress_callback: Callable[[object], None] | None = None,
        approval_callback: Callable[..., Any] | None = None,
    ) -> Message:
        existing = self._idempotency.get(
            method=self._METHOD_HANDLE_MESSAGE,
            idempotency_key=idempotency_key,
        )
        if existing is not None and existing.status == "completed":
            return _message_from_cache(existing.response)

        inflight_key = (self._METHOD_HANDLE_MESSAGE, idempotency_key)
        inflight = self._inflight.get(inflight_key)
        if inflight is not None:
            return await inflight

        request_hash = _request_hash(
            channel=channel,
            target=target,
            body=body,
            session_id=session_id,
            inbound_metadata=inbound_metadata,
            typed_turn_intent=typed_turn_intent,
        )
        reserved = self._idempotency.reserve(
            method=self._METHOD_HANDLE_MESSAGE,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if not reserved:
            existing = self._idempotency.get(
                method=self._METHOD_HANDLE_MESSAGE,
                idempotency_key=idempotency_key,
            )
            if existing is not None and existing.status == "completed":
                return _message_from_cache(existing.response)
            inflight = self._inflight.get(inflight_key)
            if inflight is not None:
                return await inflight

        task = asyncio.create_task(
            self._handle_message_once(
                channel=channel,
                target=target,
                body=body,
                session_id=session_id,
                request_id=request_id,
                inbound_metadata=inbound_metadata,
                deliver=deliver,
                forced_tools=forced_tools,
                capability_category=capability_category,
                typed_turn_intent=typed_turn_intent,
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
        forced_tools: Optional[list[str]] = None,
        capability_category: Optional[str] = None,
        typed_turn_intent: TypedTurnIntent | None = None,
        progress_callback: Callable[[object], None] | None = None,
        approval_callback: Callable[..., Any] | None = None,
    ) -> Message:
        return await self._turn_runner.run(
            channel=channel,
            target=target,
            body=body,
            session_id=session_id,
            request_id=request_id,
            inbound_metadata=inbound_metadata,
            deliver=deliver,
            forced_tools=forced_tools,
            capability_category=capability_category,
            typed_turn_intent=typed_turn_intent,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
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
        forced_tools: Optional[list[str]] = None,
        capability_category: Optional[str] = None,
        typed_turn_intent: TypedTurnIntent | None = None,
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
            forced_tools=forced_tools,
            capability_category=capability_category,
            typed_turn_intent=typed_turn_intent,
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
                    deliver=False,
                    inbound_metadata={"attach_id": attach_id},
                )
            )
            try:
                if show_progress:
                    await _await_with_progress_indicator(pending, label="openminion")
                response = await pending
                self._channels.get(response.channel).send(response)
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
    ) -> None:
        try:
            append_run_state_event(
                self._sessions,
                session_id=session_id,
                run_id=run_id,
                state=state,
                current_step=current_step,
                payload=payload,
            )
        except Exception as exc:
            self._logger.warning(
                "failed to append run event session_id=%s run_id=%s state=%s error=%s",
                session_id,
                run_id,
                state,
                exc,
            )
