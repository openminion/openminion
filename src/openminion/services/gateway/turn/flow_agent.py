from typing import Any
from collections.abc import Callable

from openminion.base.types import Message
from openminion.modules.policy import RISK_LOW
from openminion.modules.task.run import (
    RUN_STATE_RESPONDING,
    RUN_STATE_RUNNING,
    RUN_STATE_WAITING_TOOL,
)
from openminion.modules.telemetry.usage import RunStats
from openminion.services.gateway.routing import parse_metadata_bool
from openminion.services.gateway.turn.runtime import (
    _extract_ephemeral_prompt_metadata,
    _response_has_tool_activity,
)

from .flow_models import (
    _RoutingResult,
    _attach_progress_usage_metadata,
    _human_participant_id,
    _progress_usage_stats,
)


class GatewayTurnAgentExecutionMixin:
    def _emit_agent_running_state(
        self,
        *,
        routing: Any,
        run_id: str,
        lifecycle_payload: dict[str, str],
        turn_context: Any,
        setup_cost_route: Any,
        session_turn_fence_token: int | None,
    ) -> None:
        self._emit_run_state(
            session_id=routing.session.id,
            run_id=run_id,
            state=RUN_STATE_RUNNING,
            current_step="agent.generate",
            payload=self._lifecycle_ops.corr_payload(
                normalized_request_id=routing.normalized_request_id,
                lifecycle_payload=lifecycle_payload,
                extra={
                    "history_count": len(turn_context.history),
                    "memory_capsule_strategy": self._memory_capsule_strategy,
                    "memory_capsule_cache_hit": str(
                        turn_context.capsule_cache_hit
                    ).lower(),
                    "memory_capsule_chars": len(turn_context.memory_context),
                    "memory_dynamic_retrieval_enabled": str(
                        self._memory_dynamic_retrieval_enabled
                    ).lower(),
                    "memory_dynamic_retrieval_chars": len(
                        turn_context.memory_retrieval_context
                    ),
                    "setup_cost_route": setup_cost_route.label,
                    "setup_cost_route_reason": setup_cost_route.reason,
                },
            ),
            session_turn_fence_token=session_turn_fence_token,
        )

    @staticmethod
    def _usage_totals_from_response(response: Any) -> tuple[int | None, int | None]:
        metadata = getattr(response, "metadata", None)
        stats = RunStats.from_mapping(metadata if isinstance(metadata, dict) else None)
        if stats is None:
            return None, None
        return stats.input_tokens, stats.output_tokens

    def _enforce_inbound_security(
        self,
        *,
        channel: str,
        target: str,
        body: str,
        run_id: str,
        routing: _RoutingResult,
        session_turn_fence_token: int | None,
    ) -> Any:
        session_id = routing.session.id
        normalized_inbound_metadata = routing.normalized_inbound_metadata
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
            session_turn_fence_token=session_turn_fence_token,
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
            session_turn_fence_token=session_turn_fence_token,
        )
        return authenticity_decision

    def _build_inbound_message(
        self,
        routing: _RoutingResult,
        *,
        channel: str,
        target: str,
        body: str,
        run_id: str,
        authenticity_decision: Any,
        participant_id: str,
    ) -> Message:
        return Message(
            channel=channel,
            target=target,
            body=body,
            metadata={
                **_extract_ephemeral_prompt_metadata(
                    routing.normalized_inbound_metadata
                ),
                "session_id": routing.session.id,
                "run_id": run_id,
                "invocation_id": routing.normalized_inbound_metadata.get(
                    "invocation_id", ""
                ),
                "request_id": routing.normalized_request_id,
                "thread_decision_action": routing.routing_action,
                "thread_decision_reason": routing.routing_reason,
                "thread_state_before": routing.lifecycle.thread_state,
                "thread_state_qualifier": routing.lifecycle.qualifier,
                **self._lifecycle_ops.optional_ids(
                    conversation_id=routing.conversation_id,
                    thread_id=routing.thread_id,
                    attach_id=routing.attach_id,
                ),
                "origin": f"channel:{channel}",
                "untrusted_input": "false" if channel == "console" else "true",
                "untrusted_source": f"channel:{channel}",
                "authenticity_status": authenticity_decision.reason_code,
                "authenticity_verified": str(authenticity_decision.verified).lower(),
                "authenticity_mode": authenticity_decision.mode,
                "participant_id": participant_id,
                "participant_type": "human",
                "display_name": participant_id,
            },
        )

    def _persist_inbound_message(
        self,
        routing: _RoutingResult,
        *,
        channel: str,
        target: str,
        body: str,
        run_id: str,
        participant_id: str,
        session_turn_fence_token: int | None,
    ) -> None:
        self._sessions.append_message(
            session_id=routing.session.id,
            conversation_id=routing.conversation_id or None,
            thread_id=routing.thread_id or None,
            attach_id=routing.attach_id or None,
            role="inbound",
            body=body,
            metadata={
                "channel": channel,
                "target": target,
                "run_id": run_id,
                "request_id": routing.normalized_request_id,
                "thread_decision_action": routing.routing_action,
                "thread_decision_reason": routing.routing_reason,
                "thread_state_before": routing.lifecycle.thread_state,
                "thread_state_qualifier": routing.lifecycle.qualifier,
                **self._lifecycle_ops.optional_ids(
                    conversation_id=routing.conversation_id,
                    thread_id=routing.thread_id,
                    attach_id=routing.attach_id,
                ),
            },
            participant_id=participant_id,
            participant_type="human",
            display_name=participant_id,
            session_turn_fence_token=session_turn_fence_token,
        )

    def _finalize_agent_response_metadata(
        self,
        *,
        response: Any,
        routing: _RoutingResult,
        authenticity_decision: Any,
        prior_transcript_available: bool,
        latest_progress_usage: RunStats | None,
        latest_progress_usage_estimated: bool,
    ) -> None:
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
        response.metadata.setdefault("thread_decision_action", routing.routing_action)
        response.metadata.setdefault("thread_decision_reason", routing.routing_reason)
        response.metadata.setdefault(
            "thread_state_before",
            routing.lifecycle.thread_state,
        )
        response.metadata.setdefault(
            "thread_state_qualifier",
            routing.lifecycle.qualifier,
        )

    def _emit_agent_progress_states(
        self,
        *,
        response: Any,
        routing: _RoutingResult,
        run_id: str,
        lifecycle_payload: dict[str, Any],
        session_turn_fence_token: int | None,
    ) -> None:
        session_id = routing.session.id
        if _response_has_tool_activity(response.metadata):
            self._emit_run_state(
                session_id=session_id,
                run_id=run_id,
                state=RUN_STATE_WAITING_TOOL,
                current_step="tools.executed",
                payload=self._lifecycle_ops.corr_payload(
                    normalized_request_id=routing.normalized_request_id,
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
                session_turn_fence_token=session_turn_fence_token,
            )
        self._emit_run_state(
            session_id=session_id,
            run_id=run_id,
            state=RUN_STATE_RESPONDING,
            current_step="channel.send",
            payload=self._lifecycle_ops.corr_payload(
                normalized_request_id=routing.normalized_request_id,
                lifecycle_payload=lifecycle_payload,
                extra={"channel": response.channel, "target": response.target},
            ),
            session_turn_fence_token=session_turn_fence_token,
        )

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
        forced_tools: list[str] | None,
        capability_category: str | None,
        prior_transcript_available: bool,
        progress_callback: Callable[[object], None] | None = None,
        approval_callback: Callable[..., Any] | None = None,
        session_turn_fence_token: int | None = None,
    ) -> Any:
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

        participant_id = _human_participant_id(
            session=routing.session,
            channel=channel,
            target=target,
            inbound_metadata=routing.normalized_inbound_metadata,
        )
        authenticity_decision = self._enforce_inbound_security(
            channel=channel,
            target=target,
            body=body,
            run_id=run_id,
            routing=routing,
            session_turn_fence_token=session_turn_fence_token,
        )
        inbound = self._build_inbound_message(
            routing,
            channel=channel,
            target=target,
            body=body,
            run_id=run_id,
            authenticity_decision=authenticity_decision,
            participant_id=participant_id,
        )
        if not parse_metadata_bool(
            routing.normalized_inbound_metadata,
            "room_router_skip_inbound_persist",
        ):
            self._persist_inbound_message(
                routing,
                channel=channel,
                target=target,
                body=body,
                run_id=run_id,
                participant_id=participant_id,
                session_turn_fence_token=session_turn_fence_token,
            )

        response = await self._agent.run_turn(
            inbound,
            history=history,
            forced_tools=list(forced_tools or []),
            capability_category=capability_category,
            progress_callback=_capture_progress,
            approval_callback=approval_callback,
        )
        self._finalize_agent_response_metadata(
            response=response,
            routing=routing,
            authenticity_decision=authenticity_decision,
            prior_transcript_available=prior_transcript_available,
            latest_progress_usage=latest_progress_usage,
            latest_progress_usage_estimated=latest_progress_usage_estimated,
        )
        self._security.emit_agent_security_events(
            session_id=routing.session.id,
            run_id=run_id,
            metadata=response.metadata,
            session_turn_fence_token=session_turn_fence_token,
        )
        self._emit_agent_progress_states(
            response=response,
            routing=routing,
            run_id=run_id,
            lifecycle_payload=lifecycle_payload,
            session_turn_fence_token=session_turn_fence_token,
        )
        return response
