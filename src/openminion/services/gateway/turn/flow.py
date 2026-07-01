import logging
from dataclasses import dataclass, field as _dc_field
from typing import Any, Callable, Optional
from uuid import uuid4

from openminion.base.channel import ChannelRegistry
from openminion.base.types import Message
from openminion.modules.brain.constants import (
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
)
from openminion.modules.storage.runtime.session_store import SessionStore
from openminion.services.agent import AgentService
from openminion.services.context.session import SessionContextService
from openminion.services.gateway.constants import (
    CALLER_HANDLES_DELIVERY_METADATA_KEY,
)
from openminion.services.gateway.context import build_turn_context
from openminion.services.gateway.memory import MemoryFollowupQueue, record_memory_turn
from openminion.services.gateway.response import build_outbound_message
from openminion.services.gateway.routing import (
    build_lifecycle_payload,
    find_pending_outbound,
    parse_metadata_bool,
)
from openminion.services.gateway.security import GatewaySecurity
from openminion.services.gateway.turn_intent import (
    TypedTurnIntent,
    build_fail_closed_terminal_resolution,
)
from openminion.services.gateway.types import TurnContext
from openminion.services.gateway.turn.runtime import (
    _extract_ephemeral_prompt_metadata,
    _normalize_metadata,
    _response_has_tool_activity,
)
from openminion.services.gateway.turn.lifecycle import _GatewayTurnLifecycleOps
from openminion.services.runtime.run_status import (
    ATTACH_ROLE_OBSERVER,
    ATTACH_ROLE_WRITER,
    RUN_STATE_COMPLETED,
    RUN_STATE_QUEUED,
    RUN_STATE_RESPONDING,
    RUN_STATE_WAITING_TOOL,
    THREAD_DECISION_REPLAY,
    THREAD_DECISION_RESUME,
    THREAD_STATE_CANCELLED,
    THREAD_STATE_DETACHED,
    THREAD_STATE_FAILED,
    THREAD_STATE_SETTLED,
    resolve_thread_lifecycle,
    resolve_thread_routing_decision,
)
from openminion.services.security.policy import RISK_LOW
from openminion.services.stats import RunStats


def _human_participant_id(
    *,
    session: Any,
    channel: str,
    target: str,
    inbound_metadata: dict[str, str],
) -> str:
    explicit = str(
        inbound_metadata.get("participant_id")
        or inbound_metadata.get("human_id")
        or inbound_metadata.get("user")
        or ""
    ).strip()
    if explicit:
        return explicit
    metadata = getattr(session, "metadata", {}) or {}
    local_human_id = str(metadata.get("local_human_id", "") or "").strip()
    if local_human_id:
        return local_human_id
    return str(target or channel or "human").strip()


def _progress_payload_mapping(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return dict(payload)
    model_dump = getattr(payload, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    return {}


def _progress_usage_stats(payload: Any) -> tuple[RunStats | None, bool]:
    mapped = _progress_payload_mapping(payload)
    if not mapped:
        return None, False
    stats = RunStats.from_mapping(mapped)
    if stats is None:
        return None, bool(mapped.get("token_usage_estimated", False))
    return stats, bool(mapped.get("token_usage_estimated", False))


def _attach_progress_usage_metadata(
    metadata: dict[str, str],
    stats: RunStats | None,
) -> None:
    if stats is None:
        return
    existing = RunStats.from_mapping(metadata)
    if existing is not None and (
        existing.input_tokens > 0 or existing.output_tokens > 0
    ):
        return
    total_tokens = max(0, int(stats.input_tokens) + int(stats.output_tokens))
    if total_tokens <= 0:
        return
    metadata["total_input_tokens_used"] = str(int(stats.input_tokens))
    metadata["total_output_tokens_used"] = str(int(stats.output_tokens))
    metadata["total_tokens_used"] = str(total_tokens)


@dataclass
class _RoutingResult:
    """Resolved routing state for a single gateway turn."""

    early_return: Optional[Message]
    normalized_request_id: str = ""
    normalized_inbound_metadata: dict[str, str] = _dc_field(default_factory=dict)
    conversation_id: str = ""
    thread_id: str = ""
    attach_id: str = ""
    session: Any = None
    lifecycle: Any = None
    routing_action: str = ""
    routing_reason: str = ""
    resume_requested: bool = False
    reset_requested: bool = False
    auto_resume_inferred: bool = False
    explicit_conversation: bool = False
    explicit_thread: bool = False


def _response_is_pae_idle_tick_noop(response: Any) -> bool:
    metadata = getattr(response, "metadata", None) or {}
    if not isinstance(metadata, dict):
        return False
    return str(metadata.get("pae_idle_tick_noop", "")).strip().lower() == "true"


class GatewayTurnRunnerFlowMixin:
    def __init__(
        self,
        *,
        agent: AgentService,
        agent_memory: Any,
        channels: ChannelRegistry,
        logger: logging.Logger,
        sessions: SessionStore,
        session_context: SessionContextService,
        security: GatewaySecurity,
        agent_id: str,
        history_limit: int,
        memory_capsule_strategy: str,
        memory_capsule_cache: dict[str, str],
        memory_dynamic_retrieval_enabled: bool,
        emit_run_state: Callable[..., None],
        knowledge_graphs: Any | None = None,
        typed_terminal_resolver: Optional[
            Callable[..., Optional[tuple[Any, ...]]]
        ] = None,
    ) -> None:
        self._agent = agent
        self._agent_memory = agent_memory
        self._knowledge_graphs = knowledge_graphs
        self._channels = channels
        self._logger = logger
        self._sessions = sessions
        self._session_context = session_context
        self._security = security
        self._agent_id = agent_id
        self._history_limit = history_limit
        self._memory_capsule_strategy = memory_capsule_strategy
        self._memory_capsule_cache = memory_capsule_cache
        self._memory_followup_queue = MemoryFollowupQueue(auto_start=False)
        self._memory_dynamic_retrieval_enabled = memory_dynamic_retrieval_enabled
        self._emit_run_state = emit_run_state
        self._typed_terminal_resolver = typed_terminal_resolver
        self._lifecycle_ops = _GatewayTurnLifecycleOps(
            sessions=sessions,
            logger=logger,
            emit_run_state=emit_run_state,
            typed_terminal_resolver=typed_terminal_resolver,
        )

    def flush_memory_followups(self, *, session_id: str | None = None) -> None:
        self._memory_followup_queue.flush(session_id=session_id)

    def _emit_terminal_run_state(
        self,
        *,
        session_id: str,
        run_id: str,
        legacy_state: str,
        current_step: str,
        payload: Optional[dict[str, Any]] = None,
        conversation_id: str | None = None,
        thread_id: str | None = None,
        attach_id: str | None = None,
        typed_terminal_resolver: Optional[
            Callable[..., Optional[tuple[Any, ...]]]
        ] = None,
    ) -> None:
        self._lifecycle_ops.emit_terminal_run_state(
            session_id=session_id,
            run_id=run_id,
            legacy_state=legacy_state,
            current_step=current_step,
            payload=payload,
            conversation_id=conversation_id,
            thread_id=thread_id,
            attach_id=attach_id,
            typed_terminal_resolver=typed_terminal_resolver,
        )

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
            pending = find_pending_outbound(
                self._sessions,
                session_id=session.id,
                conversation_id=conversation_id or None,
                thread_id=resolved_thread_id or None,
            )
            if pending is not None:
                caller_handles_delivery = parse_metadata_bool(
                    normalized_inbound_metadata,
                    CALLER_HANDLES_DELIVERY_METADATA_KEY,
                )
                replay = Message(
                    channel=channel,
                    target=target,
                    body=pending.body,
                    metadata={
                        **pending.metadata,
                        "session_id": session.id,
                        "replayed_response": "true",
                        "pending_response_id": pending.id,
                        "thread_decision_action": routing_action,
                        "thread_decision_reason": routing_reason,
                        "thread_state_before": lifecycle.thread_state,
                        "thread_state_qualifier": lifecycle.qualifier,
                        **self._lifecycle_ops.optional_ids(
                            conversation_id=conversation_id,
                            thread_id=resolved_thread_id,
                            attach_id=attach_id,
                        ),
                    },
                    id=pending.id,
                )
                if deliver:
                    self._channels.get(channel).send(replay)
                if deliver or caller_handles_delivery:
                    self._lifecycle_ops.emit_turn_event(
                        session_id=session.id,
                        event_type="response.delivered",
                        conversation_id=conversation_id or None,
                        thread_id=resolved_thread_id or None,
                        attach_id=attach_id or None,
                        payload={
                            "run_id": lifecycle.latest_run_id,
                            "response_id": pending.id,
                            "delivery_mode": "channel" if deliver else "return",
                            "channel": channel,
                            "target": target,
                            "thread_decision_action": routing_action,
                            "thread_decision_reason": routing_reason,
                        },
                    )
                return _RoutingResult(early_return=replay)
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

        attach_role = ""
        attach_conflict = False
        allow_writer_takeover = lifecycle.thread_state in {
            THREAD_STATE_SETTLED,
            THREAD_STATE_FAILED,
            THREAD_STATE_CANCELLED,
            THREAD_STATE_DETACHED,
        }
        if attach_id and thread_id == lifecycle.thread_id:
            if lifecycle.writer_attach_id and lifecycle.writer_attach_id != attach_id:
                if allow_writer_takeover:
                    attach_role = ATTACH_ROLE_WRITER
                else:
                    attach_role = ATTACH_ROLE_OBSERVER
                    attach_conflict = True
            else:
                attach_role = ATTACH_ROLE_WRITER
        elif attach_id:
            attach_role = ATTACH_ROLE_WRITER
        if attach_role:
            self._lifecycle_ops.emit_turn_event(
                session_id=session_id,
                event_type="client.attach",
                conversation_id=conversation_id or None,
                thread_id=thread_id or None,
                attach_id=attach_id or None,
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

    @staticmethod
    def _usage_totals_from_response(response: Any) -> tuple[int | None, int | None]:
        metadata = getattr(response, "metadata", None)
        stats = RunStats.from_mapping(metadata if isinstance(metadata, dict) else None)
        if stats is None:
            return None, None
        return stats.input_tokens, stats.output_tokens

    async def _execute_agent(
        self,
        routing: _RoutingResult,
        *,
        channel: str,
        target: str,
        body: str,
        run_id: str,
        lifecycle_payload: dict[str, Any],
        history: list[Message],
        forced_tools: Optional[list[str]],
        capability_category: Optional[str],
        prior_transcript_available: bool,
        progress_callback: Callable[[object], None] | None = None,
        approval_callback: Callable[..., Any] | None = None,
    ) -> Any:
        session_id = routing.session.id
        conversation_id = routing.conversation_id
        thread_id = routing.thread_id
        attach_id = routing.attach_id
        lifecycle = routing.lifecycle
        routing_action = routing.routing_action
        routing_reason = routing.routing_reason
        normalized_request_id = routing.normalized_request_id
        normalized_inbound_metadata = routing.normalized_inbound_metadata
        latest_progress_usage: RunStats | None = None
        latest_progress_usage_estimated = False

        def _capture_progress(payload: object) -> None:
            nonlocal latest_progress_usage, latest_progress_usage_estimated
            usage_stats, is_estimated = _progress_usage_stats(payload)
            if usage_stats is not None and (
                not is_estimated or latest_progress_usage is None
            ):
                latest_progress_usage = usage_stats
                latest_progress_usage_estimated = is_estimated
            if progress_callback is not None:
                progress_callback(payload)

        inbound_participant_id = _human_participant_id(
            session=routing.session,
            channel=channel,
            target=target,
            inbound_metadata=normalized_inbound_metadata,
        )
        skip_inbound_persist = parse_metadata_bool(
            normalized_inbound_metadata,
            "room_router_skip_inbound_persist",
        )

        authenticity_decision = self._security.evaluate_inbound_authenticity(
            channel=channel,
            target=target,
            body=body,
            inbound_metadata=normalized_inbound_metadata,
        )
        self._security.enforce_inbound_authenticity(
            session_id=session_id,
            run_id=run_id,
            decision=authenticity_decision,
        )
        self._security.enforce_policy(
            session_id=session_id,
            run_id=run_id,
            decision=self._security.evaluate_policy(
                resource="gateway",
                verb="turn.execute",
                risk=RISK_LOW,
                channel=channel,
                target=target,
                session_id=session_id,
                run_id=run_id,
            ),
        )
        inbound = Message(
            channel=channel,
            target=target,
            body=body,
            metadata={
                **_extract_ephemeral_prompt_metadata(normalized_inbound_metadata),
                "session_id": session_id,
                "run_id": run_id,
                "request_id": normalized_request_id,
                "thread_decision_action": routing_action,
                "thread_decision_reason": routing_reason,
                "thread_state_before": lifecycle.thread_state,
                "thread_state_qualifier": lifecycle.qualifier,
                **self._lifecycle_ops.optional_ids(
                    conversation_id=conversation_id,
                    thread_id=thread_id,
                    attach_id=attach_id,
                ),
                "origin": f"channel:{channel}",
                "untrusted_input": "false" if channel == "console" else "true",
                "untrusted_source": f"channel:{channel}",
                "authenticity_status": authenticity_decision.reason_code,
                "authenticity_verified": str(authenticity_decision.verified).lower(),
                "authenticity_mode": authenticity_decision.mode,
                "participant_id": inbound_participant_id,
                "participant_type": "human",
                "display_name": inbound_participant_id,
            },
        )
        if not skip_inbound_persist:
            self._sessions.append_message(
                session_id=session_id,
                conversation_id=conversation_id or None,
                thread_id=thread_id or None,
                attach_id=attach_id or None,
                role="inbound",
                body=body,
                metadata={
                    "channel": channel,
                    "target": target,
                    "run_id": run_id,
                    "request_id": normalized_request_id,
                    "thread_decision_action": routing_action,
                    "thread_decision_reason": routing_reason,
                    "thread_state_before": lifecycle.thread_state,
                    "thread_state_qualifier": lifecycle.qualifier,
                    **self._lifecycle_ops.optional_ids(
                        conversation_id=conversation_id,
                        thread_id=thread_id,
                        attach_id=attach_id,
                    ),
                },
                participant_id=inbound_participant_id,
                participant_type="human",
                display_name=inbound_participant_id,
            )

        response = await self._agent.run_turn(
            inbound,
            history=history,
            forced_tools=list(forced_tools or []),
            capability_category=capability_category,
            progress_callback=_capture_progress,
            approval_callback=approval_callback,
        )
        if not latest_progress_usage_estimated:
            _attach_progress_usage_metadata(response.metadata, latest_progress_usage)
        response.metadata.setdefault(
            "authenticity_status", authenticity_decision.reason_code
        )
        response.metadata.setdefault(
            "authenticity_verified",
            str(authenticity_decision.verified).lower(),
        )
        response.metadata.setdefault("authenticity_mode", authenticity_decision.mode)
        response.metadata.setdefault(
            "session_history_available",
            str(prior_transcript_available).lower(),
        )
        response.metadata.setdefault("thread_decision_action", routing_action)
        response.metadata.setdefault("thread_decision_reason", routing_reason)
        response.metadata.setdefault("thread_state_before", lifecycle.thread_state)
        response.metadata.setdefault("thread_state_qualifier", lifecycle.qualifier)
        if _response_has_tool_activity(response.metadata):
            self._emit_run_state(
                session_id=session_id,
                run_id=run_id,
                state=RUN_STATE_WAITING_TOOL,
                current_step="tools.executed",
                payload=self._lifecycle_ops.corr_payload(
                    normalized_request_id=normalized_request_id,
                    lifecycle_payload=lifecycle_payload,
                    extra={
                        "tool_calls_count": response.metadata.get(
                            "tool_calls_count", "0"
                        ),
                        "tool_execution_count": response.metadata.get(
                            "tool_execution_count", "0"
                        ),
                    },
                ),
            )
        self._security.emit_agent_security_events(
            session_id=session_id,
            run_id=run_id,
            metadata=response.metadata,
        )
        self._emit_run_state(
            session_id=session_id,
            run_id=run_id,
            state=RUN_STATE_RESPONDING,
            current_step="channel.send",
            payload=self._lifecycle_ops.corr_payload(
                normalized_request_id=normalized_request_id,
                lifecycle_payload=lifecycle_payload,
                extra={"channel": response.channel, "target": response.target},
            ),
        )
        return response

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
            )
        self._lifecycle_ops.emit_turn_event(
            session_id=session_id,
            event_type="response.persisted",
            conversation_id=conversation_id or None,
            thread_id=thread_id or None,
            attach_id=attach_id or None,
            payload={"run_id": run_id, "response_id": outbound_record.id},
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
