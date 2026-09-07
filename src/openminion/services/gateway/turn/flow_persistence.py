from typing import TYPE_CHECKING, Any
from collections.abc import Callable

from openminion.base.types import Message
from openminion.modules.brain.constants import (
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
)
from openminion.modules.policy import RISK_LOW
from openminion.modules.task.run import RUN_STATE_COMPLETED
from openminion.modules.telemetry.trace.phase_timing import active_chat_phase
from openminion.services.gateway.constants import CALLER_HANDLES_DELIVERY_METADATA_KEY
from openminion.services.gateway.memory import record_memory_turn
from openminion.services.gateway.response import build_outbound_message
from openminion.services.gateway.routing import parse_metadata_bool

from openminion.modules.session.capture import verify_terminal_capture_receipt
from .flow_models import _RoutingResult

if TYPE_CHECKING:
    from openminion.modules.storage.runtime.session_store import SessionStore


class GatewayTurnPersistenceDeliveryMixin:
    _sessions: "SessionStore"
    _agent: Any

    def _finish_run_record(
        self,
        run_id: str,
        *,
        status: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        if not hasattr(self._sessions, "finish_run_record"):
            return
        kwargs: dict[str, Any] = {"status": status}
        if input_tokens is not None:
            kwargs["input_tokens"] = input_tokens
        if output_tokens is not None:
            kwargs["output_tokens"] = output_tokens
        with active_chat_phase("run_record_finish"):
            self._sessions.finish_run_record(run_id, **kwargs)

    def _suppressed_outbound_for_response(
        self,
        *,
        routing: _RoutingResult,
        run_id: str,
        response: Any,
    ) -> Message:
        metadata: dict[str, str] = {
            "run_id": run_id,
            "run_state": RUN_STATE_COMPLETED,
            "pae_idle_tick_noop": "true",
            "suppressed": "pae_idle_tick_noop",
        }
        if routing.normalized_request_id:
            metadata["request_id"] = routing.normalized_request_id
        if routing.conversation_id:
            metadata["conversation_id"] = routing.conversation_id
        if routing.thread_id:
            metadata["thread_id"] = routing.thread_id
        if routing.attach_id:
            metadata["attach_id"] = routing.attach_id
        return Message(
            channel=str(getattr(response, "channel", "") or ""),
            target=str(getattr(response, "target", "") or ""),
            body="",
            metadata=metadata,
        )

    def _complete_suppressed_idle_tick(
        self,
        *,
        routing: _RoutingResult,
        run_id: str,
        response: Any,
        lifecycle_payload: dict[str, str],
        typed_terminal_resolver: Callable[..., Any] | None,
        session_turn_fence_token: int | None,
    ) -> Message:
        self._finish_run_record(
            run_id,
            status="completed",
            input_tokens=0,
            output_tokens=0,
        )
        outbound = self._suppressed_outbound_for_response(
            routing=routing,
            run_id=run_id,
            response=response,
        )
        self._lifecycle_ops.emit_turn_event(
            session_id=routing.session.id,
            event_type="response.suppressed",
            conversation_id=routing.conversation_id or None,
            thread_id=routing.thread_id or None,
            attach_id=routing.attach_id or None,
            payload={"run_id": run_id, "reason": "pae_idle_tick_noop"},
            session_turn_fence_token=session_turn_fence_token,
        )
        with active_chat_phase("terminal_event"):
            self._lifecycle_ops.emit_terminal_run_state(
                session_id=routing.session.id,
                run_id=run_id,
                legacy_state=RUN_STATE_COMPLETED,
                current_step="turn.completed",
                payload=self._lifecycle_ops.corr_payload(
                    normalized_request_id=routing.normalized_request_id,
                    lifecycle_payload=lifecycle_payload,
                    extra={
                        "response_chars": 0,
                        "suppressed": "pae_idle_tick_noop",
                    },
                ),
                conversation_id=routing.conversation_id or None,
                thread_id=routing.thread_id or None,
                attach_id=routing.attach_id or None,
                typed_terminal_resolver=typed_terminal_resolver,
                session_turn_fence_token=session_turn_fence_token,
            )
        return outbound

    def _build_outbound_and_persist(
        self,
        routing: _RoutingResult,
        *,
        run_id: str,
        response: Any,
        memory_context_meta: dict[str, str],
        memory_retrieval_meta: dict[str, str],
        session_turn_fence_token: int | None = None,
    ) -> tuple[Message, Any]:
        session_id = routing.session.id
        conversation_id = routing.conversation_id
        thread_id = routing.thread_id
        attach_id = routing.attach_id
        normalized_request_id = routing.normalized_request_id

        verify_terminal_capture_receipt(
            sessions=self._sessions,
            response_metadata=response.metadata,
            session_id=session_id,
            run_id=run_id,
            required=bool(
                getattr(self._agent, "memory_capture_assurance_enabled", False)
            ),
        )

        self._security.enforce_policy(
            session_id=session_id,
            run_id=run_id,
            decision=self._security.evaluate_policy(
                resource="channel",
                verb="message.send",
                risk=RISK_LOW,
                channel=response.channel,
                target=response.target,
                session_id=session_id,
                run_id=run_id,
            ),
            session_turn_fence_token=session_turn_fence_token,
        )
        outbound = build_outbound_message(
            response=response,
            session_id=session_id,
            run_id=run_id,
            request_id=normalized_request_id,
            conversation_id=conversation_id,
            thread_id=thread_id,
            attach_id=attach_id,
            memory_context_meta=memory_context_meta,
            memory_retrieval_meta=memory_retrieval_meta,
        )
        outbound.metadata["run_state"] = RUN_STATE_COMPLETED
        if (
            outbound.metadata.get("respond_kind", "").strip()
            == RESPOND_KIND_POLICY_CONFIRMATION_PROMPT
        ):
            outbound_record: Any = self._sessions.append_event(
                session_id=session_id,
                event_type=SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
                payload={
                    "body": outbound.body,
                    "conversation_id": conversation_id or "",
                    "thread_id": thread_id or "",
                    "attach_id": attach_id or "",
                    "run_id": run_id,
                    "request_id": normalized_request_id,
                },
                session_turn_fence_token=session_turn_fence_token,
            )
        else:
            outbound_record = self._sessions.append_message(
                session_id=session_id,
                conversation_id=conversation_id or None,
                thread_id=thread_id or None,
                attach_id=attach_id or None,
                role="outbound",
                body=outbound.body,
                metadata=outbound.metadata,
                participant_id=self._agent_id,
                participant_type="agent",
                display_name=self._agent_id,
                session_turn_fence_token=session_turn_fence_token,
            )
        outbound.metadata["persisted_outbound_message_id"] = outbound_record.id
        self._lifecycle_ops.emit_turn_event(
            session_id=session_id,
            event_type="response.persisted",
            conversation_id=conversation_id or None,
            thread_id=thread_id or None,
            attach_id=attach_id or None,
            payload={"run_id": run_id, "response_id": outbound_record.id},
            session_turn_fence_token=session_turn_fence_token,
        )
        return outbound, outbound_record

    def _write_turn_memory(
        self,
        routing: _RoutingResult,
        *,
        channel: str,
        target: str,
        body: str,
        run_id: str,
        outbound: Message,
        session_turn_fence_token: int | None = None,
    ) -> None:
        session_id = routing.session.id
        conversation_id = routing.conversation_id
        thread_id = routing.thread_id
        attach_id = routing.attach_id
        normalized_request_id = routing.normalized_request_id

        policy_route = outbound.metadata.get("memory_policy_route", "").strip()
        if policy_route:
            policy_payload = {
                "run_id": run_id,
                "request_id": normalized_request_id,
                "route": policy_route,
                "source": outbound.metadata.get(
                    "memory_policy_source", "runtime.config"
                )
                or "runtime.config",
                "version": outbound.metadata.get("memory_policy_version", ""),
                "reason_code": outbound.metadata.get("reason_code", ""),
            }
            policy_error = outbound.metadata.get("memory_policy_error", "").strip()
            if policy_error:
                policy_payload["error"] = policy_error
            self._lifecycle_ops.emit_memory_event(
                session_id=session_id,
                event_type="memory.policy.snapshot",
                conversation_id=conversation_id or None,
                thread_id=thread_id or None,
                attach_id=attach_id or None,
                payload=policy_payload,
                session_turn_fence_token=session_turn_fence_token,
            )
        record_memory_turn(
            agent_memory=self._agent_memory,
            logger=self._logger,
            agent_id=self._agent_id,
            memory_capsule_strategy=self._memory_capsule_strategy,
            memory_capsule_cache=self._memory_capsule_cache,
            session_id=session_id,
            run_id=run_id,
            request_id=normalized_request_id,
            channel=channel,
            target=target,
            user_message=body,
            assistant_message=outbound.body,
            conversation_id=conversation_id,
            thread_id=thread_id,
            attach_id=attach_id,
            emit_memory_event=self._lifecycle_ops.emit_memory_event,
            outbound_metadata=outbound.metadata,
            followup_queue=self._memory_followup_queue,
            defer_followup=True,
            session_turn_fence_token=session_turn_fence_token,
            emit_memory_followup=self._emit_memory_followup,
        )

    def _deliver_and_complete(
        self,
        routing: _RoutingResult,
        *,
        channel: str,
        target: str,
        run_id: str,
        lifecycle_payload: dict[str, Any],
        response: Any,
        outbound: Message,
        outbound_record: Any,
        deliver: bool,
        typed_terminal_resolver: Callable[..., tuple[Any, ...] | None] | None = None,
        session_turn_lease_owner: str = "",
        session_turn_fence_token: int | None = None,
    ) -> None:
        session_id = routing.session.id
        conversation_id = routing.conversation_id
        thread_id = routing.thread_id
        attach_id = routing.attach_id
        normalized_request_id = routing.normalized_request_id
        caller_handles_delivery = parse_metadata_bool(
            routing.normalized_inbound_metadata,
            CALLER_HANDLES_DELIVERY_METADATA_KEY,
        )

        if deliver:
            with active_chat_phase("response_delivery"):
                self._renew_session_turn_lease(
                    session_id=session_id,
                    owner=session_turn_lease_owner,
                    fence_token=session_turn_fence_token,
                )
                self._channels.get(response.channel).send(outbound)
        if deliver or caller_handles_delivery:
            with active_chat_phase("response_delivered_event"):
                self._lifecycle_ops.emit_turn_event(
                    session_id=session_id,
                    event_type="response.delivered",
                    conversation_id=conversation_id or None,
                    thread_id=thread_id or None,
                    attach_id=attach_id or None,
                    payload={
                        "run_id": run_id,
                        "response_id": outbound_record.id,
                        "delivery_mode": "channel" if deliver else "return",
                        "channel": response.channel,
                        "target": response.target,
                    },
                    session_turn_fence_token=session_turn_fence_token,
                )
        with active_chat_phase("terminal_event"):
            self._lifecycle_ops.emit_terminal_run_state(
                session_id=session_id,
                run_id=run_id,
                legacy_state=RUN_STATE_COMPLETED,
                current_step="turn.completed",
                payload=self._lifecycle_ops.corr_payload(
                    normalized_request_id=normalized_request_id,
                    lifecycle_payload=lifecycle_payload,
                    extra={
                        "response_chars": str(len(outbound.body)),
                        "provider": response.metadata.get("provider", ""),
                        "model": response.metadata.get("model", ""),
                    },
                ),
                conversation_id=conversation_id or None,
                thread_id=thread_id or None,
                attach_id=attach_id or None,
                typed_terminal_resolver=typed_terminal_resolver,
                session_turn_fence_token=session_turn_fence_token,
            )
        self._logger.info(
            "gateway turn complete channel=%s target=%s session_id=%s run_id=%s request_id=%s",
            channel,
            target,
            session_id,
            run_id,
            normalized_request_id,
        )
