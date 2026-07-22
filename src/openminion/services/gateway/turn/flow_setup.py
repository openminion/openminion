from __future__ import annotations

from typing import Any, Optional
from collections.abc import Callable
from uuid import uuid4

from openminion.base.types import Message
from openminion.modules.task.run import (
    ATTACH_ROLE_OBSERVER,
    ATTACH_ROLE_WRITER,
    RUN_STATE_QUEUED,
    THREAD_STATE_CANCELLED,
    THREAD_STATE_DETACHED,
    THREAD_STATE_FAILED,
    THREAD_STATE_SETTLED,
)
from openminion.services.gateway.context import build_turn_context
from openminion.services.gateway.routing import build_lifecycle_payload
from openminion.services.gateway.turn_intent import (
    TypedTurnIntent,
    build_fail_closed_terminal_resolution,
)
from openminion.services.gateway.types import TurnContext

from .flow_models import _RoutingResult


class GatewayTurnSetupMixin:
    def _resolve_attach_role(self, routing: _RoutingResult) -> tuple[str, bool]:
        lifecycle = routing.lifecycle
        attach_role = ""
        attach_conflict = False
        allow_writer_takeover = lifecycle.thread_state in {
            THREAD_STATE_SETTLED,
            THREAD_STATE_FAILED,
            THREAD_STATE_CANCELLED,
            THREAD_STATE_DETACHED,
        }
        if routing.attach_id and routing.thread_id == lifecycle.thread_id:
            if lifecycle.writer_attach_id and lifecycle.writer_attach_id != routing.attach_id:
                if allow_writer_takeover:
                    attach_role = ATTACH_ROLE_WRITER
                else:
                    attach_role = ATTACH_ROLE_OBSERVER
                    attach_conflict = True
            else:
                attach_role = ATTACH_ROLE_WRITER
        elif routing.attach_id:
            attach_role = ATTACH_ROLE_WRITER
        return attach_role, attach_conflict

    def _emit_attach_role_event(
        self,
        routing: _RoutingResult,
        *,
        attach_role: str,
        attach_conflict: bool,
    ) -> None:
        if not attach_role:
            return
        session_id = routing.session.id
        lifecycle = routing.lifecycle
        self._lifecycle_ops.emit_turn_event(
            session_id=session_id,
            event_type="client.attach",
            conversation_id=routing.conversation_id or None,
            thread_id=routing.thread_id or None,
            attach_id=routing.attach_id or None,
            payload={
                "attach_role": attach_role,
                "attach_conflict": str(attach_conflict).lower(),
                **(
                    {"writer_attach_id": lifecycle.writer_attach_id}
                    if attach_conflict and lifecycle.writer_attach_id
                    else {}
                ),
            },
        )

    def _setup_turn(
        self,
        routing: _RoutingResult,
        *,
        channel: str,
        target: str,
    ) -> tuple[str, dict[str, str]]:
        session_id = routing.session.id
        conversation_id = routing.conversation_id
        thread_id = routing.thread_id
        attach_id = routing.attach_id
        lifecycle = routing.lifecycle
        routing_action = routing.routing_action
        routing_reason = routing.routing_reason
        normalized_request_id = routing.normalized_request_id

        attach_role, attach_conflict = self._resolve_attach_role(routing)
        self._emit_attach_role_event(
            routing,
            attach_role=attach_role,
            attach_conflict=attach_conflict,
        )
        if attach_conflict:
            raise RuntimeError(
                f"attach conflict: writer={lifecycle.writer_attach_id or 'unknown'}"
            )
        self._lifecycle_ops.emit_turn_event(
            session_id=session_id,
            event_type="thread.decision",
            conversation_id=conversation_id or None,
            thread_id=thread_id or None,
            attach_id=attach_id or None,
            payload={
                "action": routing_action,
                "reason_code": routing_reason,
                "thread_state_before": lifecycle.thread_state,
                "thread_state_qualifier": lifecycle.qualifier,
                "resume_requested": str(routing.resume_requested).lower(),
                "reset_requested": str(routing.reset_requested).lower(),
                "explicit_conversation": str(routing.explicit_conversation).lower(),
                "explicit_thread": str(routing.explicit_thread).lower(),
                "auto_resume_inferred": str(routing.auto_resume_inferred).lower(),
            },
        )
        lifecycle_payload = build_lifecycle_payload(
            conversation_id=conversation_id,
            thread_id=thread_id,
            attach_id=attach_id,
            routing_action=routing_action,
            routing_reason=routing_reason,
            thread_state=lifecycle.thread_state,
            qualifier=lifecycle.qualifier,
        )
        run_id = uuid4().hex
        if hasattr(self._sessions, "create_run_record"):
            self._sessions.create_run_record(
                session_id,
                run_type="llm",
                run_id=run_id,
                meta={
                    "request_id": normalized_request_id,
                    "channel": channel,
                    "target": target,
                    **self._lifecycle_ops.optional_ids(
                        conversation_id=conversation_id,
                        thread_id=thread_id,
                        attach_id=attach_id,
                    ),
                },
            )
        self._emit_run_state(
            session_id=session_id,
            run_id=run_id,
            state=RUN_STATE_QUEUED,
            current_step="turn.accepted",
            payload=self._lifecycle_ops.corr_payload(
                normalized_request_id=normalized_request_id,
                lifecycle_payload=lifecycle_payload,
                extra={"channel": channel, "target": target},
            ),
        )
        return run_id, lifecycle_payload

    def _build_memory_context(
        self,
        routing: _RoutingResult,
        *,
        channel: str,
        target: str,
        body: str,
        run_id: str,
        history: list[Message],
    ) -> TurnContext:
        self._memory_followup_queue.flush(session_id=routing.session.id)
        return build_turn_context(
            history=history,
            agent_id=self._agent_id,
            agent_memory=self._agent_memory,
            logger=self._logger,
            emit_memory_event=self._lifecycle_ops.emit_memory_event,
            session_id=routing.session.id,
            run_id=run_id,
            request_id=routing.normalized_request_id,
            channel=channel,
            target=target,
            user_message=body,
            conversation_id=routing.conversation_id,
            thread_id=routing.thread_id,
            attach_id=routing.attach_id,
            memory_capsule_strategy=self._memory_capsule_strategy,
            memory_capsule_cache=self._memory_capsule_cache,
            memory_dynamic_retrieval_enabled=self._memory_dynamic_retrieval_enabled,
            knowledge_graphs=self._knowledge_graphs,
        )

    def _build_gtgs_terminal_resolver(
        self,
        *,
        typed_turn_intent: TypedTurnIntent | None,
    ) -> Optional[Callable[..., Optional[tuple[Any, ...]]]]:
        if typed_turn_intent is None:
            return None

        def _resolver(
            *, run_id: str, session_id: str, legacy_state: str
        ) -> tuple[Any, ...] | None:
            del legacy_state
            return build_fail_closed_terminal_resolution(
                turn_intent=typed_turn_intent,
                run_id=run_id,
                session_id=session_id,
                agent_id=self._agent_id,
                session_api=self._sessions,
            )

        return _resolver
