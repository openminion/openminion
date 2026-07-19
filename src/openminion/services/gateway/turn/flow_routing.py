from __future__ import annotations

from typing import Optional
from uuid import uuid4

from openminion.base.types import Message
from openminion.modules.task.run import (
    THREAD_DECISION_REPLAY,
    THREAD_DECISION_RESUME,
    resolve_thread_lifecycle,
    resolve_thread_routing_decision,
)
from openminion.services.gateway.constants import CALLER_HANDLES_DELIVERY_METADATA_KEY
from openminion.services.gateway.routing import find_pending_outbound, parse_metadata_bool
from openminion.services.gateway.turn.runtime import _normalize_metadata

from .flow_models import _RoutingResult


class GatewayTurnRoutingMixin:
    def _pending_replay_response(
        self,
        *,
        channel: str,
        target: str,
        deliver: bool,
        routing_action: str,
        routing_reason: str,
        routing: _RoutingResult,
    ) -> _RoutingResult | None:
        pending = find_pending_outbound(
            self._sessions,
            session_id=routing.session.id,
            conversation_id=routing.conversation_id or None,
            thread_id=routing.thread_id or None,
        )
        if pending is None:
            return None
        caller_handles_delivery = parse_metadata_bool(
            routing.normalized_inbound_metadata,
            CALLER_HANDLES_DELIVERY_METADATA_KEY,
        )
        replay = Message(
            channel=channel,
            target=target,
            body=pending.body,
            metadata={
                **pending.metadata,
                "session_id": routing.session.id,
                "replayed_response": "true",
                "pending_response_id": pending.id,
                "thread_decision_action": routing_action,
                "thread_decision_reason": routing_reason,
                "thread_state_before": routing.lifecycle.thread_state,
                "thread_state_qualifier": routing.lifecycle.qualifier,
                **self._lifecycle_ops.optional_ids(
                    conversation_id=routing.conversation_id,
                    thread_id=routing.thread_id,
                    attach_id=routing.attach_id,
                ),
            },
            id=pending.id,
        )
        if deliver:
            self._channels.get(channel).send(replay)
        if deliver or caller_handles_delivery:
            self._lifecycle_ops.emit_turn_event(
                session_id=routing.session.id,
                event_type="response.delivered",
                conversation_id=routing.conversation_id or None,
                thread_id=routing.thread_id or None,
                attach_id=routing.attach_id or None,
                payload={
                    "run_id": routing.lifecycle.latest_run_id,
                    "response_id": pending.id,
                    "delivery_mode": "channel" if deliver else "return",
                    "channel": channel,
                    "target": target,
                    "thread_decision_action": routing_action,
                    "thread_decision_reason": routing_reason,
                },
            )
        return _RoutingResult(early_return=replay)

    def _resolve_routing(
        self,
        *,
        channel: str,
        target: str,
        session_id: Optional[str],
        request_id: Optional[str],
        inbound_metadata: Optional[dict[str, str]],
        deliver: bool,
    ) -> _RoutingResult:
        normalized_request_id = str(request_id or "").strip() or uuid4().hex
        normalized_inbound_metadata = _normalize_metadata(inbound_metadata)
        conversation_id = str(
            normalized_inbound_metadata.get("conversation_id", "") or ""
        ).strip()
        thread_id = str(normalized_inbound_metadata.get("thread_id", "") or "").strip()
        attach_id = str(normalized_inbound_metadata.get("attach_id", "") or "").strip()
        resume_requested = parse_metadata_bool(normalized_inbound_metadata, "resume")
        reset_requested = parse_metadata_bool(
            normalized_inbound_metadata, "reset_session"
        ) or parse_metadata_bool(normalized_inbound_metadata, "reset")
        auto_resume_inferred = False
        session = self._sessions.resolve_session(
            agent_id=self._agent_id,
            channel=channel,
            target=target,
            session_id=session_id,
        )
        if (
            not resume_requested
            and not reset_requested
            and not str(session_id or "").strip()
        ):
            resume_requested = True
            auto_resume_inferred = True
        explicit_conversation = bool(conversation_id)
        explicit_thread = bool(thread_id)
        if explicit_thread:
            resume_requested = True

        lifecycle = resolve_thread_lifecycle(
            self._sessions,
            session_id=session.id,
            conversation_id=conversation_id or None,
            thread_id=thread_id or None,
        )
        routing_decision = resolve_thread_routing_decision(
            lifecycle=lifecycle,
            session_id=session.id,
            conversation_id=conversation_id,
            requested_thread_id=thread_id,
            attach_id=attach_id,
            resume_requested=resume_requested,
            reset_requested=reset_requested,
            explicit_thread=explicit_thread,
            auto_resume_inferred=auto_resume_inferred,
        )
        resolved_thread_id = routing_decision.thread_id
        routing_action = routing_decision.action
        routing_reason = routing_decision.reason_code

        if routing_action == THREAD_DECISION_REPLAY:
            replay = self._pending_replay_response(
                channel=channel,
                target=target,
                deliver=deliver,
                routing_action=routing_action,
                routing_reason=routing_reason,
                routing=_RoutingResult(
                    early_return=None,
                    normalized_inbound_metadata=normalized_inbound_metadata,
                    conversation_id=conversation_id,
                    thread_id=resolved_thread_id,
                    attach_id=attach_id,
                    session=session,
                    lifecycle=lifecycle,
                ),
            )
            if replay is not None:
                return replay
            routing_action = THREAD_DECISION_RESUME
            routing_reason = "pending_response_missing"

        return _RoutingResult(
            early_return=None,
            normalized_request_id=normalized_request_id,
            normalized_inbound_metadata=normalized_inbound_metadata,
            conversation_id=conversation_id,
            thread_id=resolved_thread_id,
            attach_id=attach_id,
            session=session,
            lifecycle=lifecycle,
            routing_action=routing_action,
            routing_reason=routing_reason,
            resume_requested=resume_requested,
            reset_requested=reset_requested,
            auto_resume_inferred=auto_resume_inferred,
            explicit_conversation=explicit_conversation,
            explicit_thread=explicit_thread,
        )
