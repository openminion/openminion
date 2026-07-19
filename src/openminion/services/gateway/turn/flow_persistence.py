from __future__ import annotations

from typing import Any, Callable, Optional

from openminion.base.types import Message
from openminion.modules.brain.constants import (
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
)
from openminion.modules.policy import RISK_LOW
from openminion.modules.task.run import RUN_STATE_COMPLETED
from openminion.services.gateway.constants import CALLER_HANDLES_DELIVERY_METADATA_KEY
from openminion.services.gateway.memory import record_memory_turn
from openminion.services.gateway.response import build_outbound_message
from openminion.services.gateway.routing import parse_metadata_bool

from .flow_models import _RoutingResult


class GatewayTurnPersistenceDeliveryMixin:
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
            str(outbound.metadata.get("respond_kind", "") or "").strip()
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

        policy_route = str(
            outbound.metadata.get("memory_policy_route", "") or ""
        ).strip()
        if policy_route:
            policy_payload = {
                "run_id": run_id,
                "request_id": normalized_request_id,
                "route": policy_route,
                "source": str(
                    outbound.metadata.get("memory_policy_source", "runtime.config")
                    or "runtime.config"
                ),
                "version": str(
                    outbound.metadata.get("memory_policy_version", "") or ""
                ),
                "reason_code": str(outbound.metadata.get("reason_code", "") or ""),
            }
            policy_error = str(
                outbound.metadata.get("memory_policy_error", "") or ""
            ).strip()
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
        typed_terminal_resolver: Optional[
            Callable[..., Optional[tuple[Any, ...]]]
        ] = None,
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
            self._channels.get(response.channel).send(outbound)
        if deliver or caller_handles_delivery:
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
            )
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
        )
        self._logger.info(
            "gateway turn complete channel=%s target=%s session_id=%s run_id=%s request_id=%s",
            channel,
            target,
            session_id,
            run_id,
            normalized_request_id,
        )
