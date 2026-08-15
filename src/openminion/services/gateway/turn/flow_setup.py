from datetime import datetime
from functools import partial
from typing import Any, Optional
from collections.abc import Callable
from uuid import uuid4

from openminion.base.types import Message
from openminion.modules.telemetry.trace.phase_timing import active_chat_phase
from openminion.modules.telemetry.events.catalog import AGENT_INVOCATION_STARTED
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
from openminion.services.agent.telemetry import InvocationLifecycleFact

from .flow_models import _RoutingResult


class GatewayTurnSetupMixin:
    def _resolve_invocation_id(self, *, lifecycle: Any) -> tuple[str, str, str]:
        prior_invocation_id = str(lifecycle.invocation_id or "").strip()
        if not prior_invocation_id:
            reason = (
                "legacy_thread_without_invocation"
                if lifecycle.invocation_source_event_id
                else "new_thread"
            )
            return uuid4().hex, reason, ""
        terminal_states = (
            THREAD_STATE_SETTLED,
            THREAD_STATE_FAILED,
            THREAD_STATE_CANCELLED,
        )
        if lifecycle.thread_state in terminal_states:
            return uuid4().hex, "terminal_parent", prior_invocation_id
        return prior_invocation_id, "resumed_thread", ""

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
            if (
                lifecycle.writer_attach_id
                and lifecycle.writer_attach_id != routing.attach_id
            ):
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
        session_turn_fence_token: int | None,
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
            session_turn_fence_token=session_turn_fence_token,
        )

    def _emit_invocation_start(
        self,
        routing: _RoutingResult,
        *,
        run_id: str,
        queued_event: Any,
        invocation_id: str,
        invocation_reason: str,
        parent_invocation_id: str,
    ) -> None:
        if queued_event is None:
            return
        source_event_id = int(queued_event.id)
        source_timestamp = queued_event.created_at
        if invocation_reason == "resumed_thread":
            source_event_id = int(routing.lifecycle.invocation_source_event_id)
            source_timestamp = routing.lifecycle.invocation_started_at
        payload: dict[str, Any] = {
            "scope": "durable",
            "source_event_id": source_event_id,
            "source_event_type": "run.queued",
            "run_id": run_id,
            "thread_id": routing.thread_id,
        }
        if parent_invocation_id:
            payload["parent_invocation_id"] = parent_invocation_id
        self._lifecycle_ops.emit_invocation_lifecycle(
            InvocationLifecycleFact(
                event_id=f"agent.invocation:{invocation_id}:start",
                timestamp=datetime.fromisoformat(source_timestamp).timestamp(),
                event_type=AGENT_INVOCATION_STARTED,
                invocation_id=invocation_id,
                session_id=routing.session.id,
                turn_id=routing.normalized_request_id,
                agent_id=self._agent_id,
                payload=payload,
            )
        )

    def _setup_turn(
        self,
        routing: _RoutingResult,
        *,
        channel: str,
        target: str,
        session_turn_fence_token: int | None = None,
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
            session_turn_fence_token=session_turn_fence_token,
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
            session_turn_fence_token=session_turn_fence_token,
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
        invocation_id, invocation_reason, parent_invocation_id = (
            self._resolve_invocation_id(lifecycle=lifecycle)
        )
        lifecycle_payload["invocation_id"] = invocation_id
        lifecycle_payload["invocation_reason"] = invocation_reason
        routing.normalized_inbound_metadata["invocation_id"] = invocation_id
        run_id = uuid4().hex
        queued_event = self._emit_run_state(
            session_id=session_id,
            run_id=run_id,
            state=RUN_STATE_QUEUED,
            current_step="turn.accepted",
            payload=self._lifecycle_ops.corr_payload(
                normalized_request_id=normalized_request_id,
                lifecycle_payload=lifecycle_payload,
                extra={
                    "agent_id": self._agent_id,
                    "channel": channel,
                    "target": target,
                },
            ),
            session_turn_fence_token=session_turn_fence_token,
        )
        self._emit_invocation_start(
            routing,
            run_id=run_id,
            queued_event=queued_event,
            invocation_id=invocation_id,
            invocation_reason=invocation_reason,
            parent_invocation_id=parent_invocation_id,
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
        session_turn_fence_token: int | None = None,
    ) -> TurnContext:
        with active_chat_phase("memory_followup_flush"):
            self._memory_followup_queue.flush(session_id=routing.session.id)
        return build_turn_context(
            history=history,
            agent_id=self._agent_id,
            agent_memory=self._agent_memory,
            logger=self._logger,
            emit_memory_event=partial(
                self._lifecycle_ops.emit_memory_event,
                session_turn_fence_token=session_turn_fence_token,
            ),
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
