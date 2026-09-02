from typing import Any, Callable, Optional

from openminion.base.types import Message
from openminion.modules.brain.constants import (
    RESPOND_KIND_ASSISTANT as RESPOND_KIND_ASSISTANT,
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT as RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
    SESSION_EVENT_POLICY_CONFIRMATION_PROMPT as SESSION_EVENT_POLICY_CONFIRMATION_PROMPT,
)
from openminion.modules.telemetry.trace.phase_timing import active_chat_phase
from openminion.services.gateway.turn_intent import TypedTurnIntent
from openminion.services.gateway.turn.flow import (
    GatewayTurnRunnerFlowMixin,
    _response_is_pae_idle_tick_noop,
)
from openminion.services.gateway.turn.route_classification import (
    classify_setup_cost_route,
)
from openminion.services.gateway.routing import parse_metadata_bool
from openminion.services.gateway.turn.lifecycle import RuntimeSessionTurnFenceError
from openminion.modules.task.run import (
    RUN_STATE_FAILED,
)


class GatewayTurnRunner(GatewayTurnRunnerFlowMixin):
    async def _compact_session_if_needed(
        self,
        *,
        routing: Any,
        session_turn_fence_token: int | None,
    ) -> None:
        if parse_metadata_bool(
            routing.normalized_inbound_metadata,
            "room_router_skip_session_compaction",
        ):
            return
        try:
            with active_chat_phase("session_compaction"):
                await self._session_context.acompact_session(
                    session_id=routing.session.id,
                    session_turn_fence_token=session_turn_fence_token,
                )
        except RuntimeSessionTurnFenceError:
            raise
        except Exception as exc:
            self._logger.warning(
                "session context compaction failed session_id=%s error=%s",
                routing.session.id,
                exc,
            )

    async def run(
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
        progress_callback: Callable[[object], None] | None = None,
        approval_callback: Callable[..., Any] | None = None,
        resolved_session: Any | None = None,
        session_turn_lease_owner: str = "",
        session_turn_fence_token: int | None = None,
    ) -> Message:
        with active_chat_phase("gateway_routing"):
            routing = self._resolve_routing(
                channel=channel,
                target=target,
                session_id=session_id,
                request_id=request_id,
                inbound_metadata=inbound_metadata,
                deliver=deliver,
                resolved_session=resolved_session,
                session_turn_lease_owner=session_turn_lease_owner,
                session_turn_fence_token=session_turn_fence_token,
            )
        if routing.early_return is not None:
            return routing.early_return

        with active_chat_phase("gateway_session_context"):
            run_id, lifecycle_payload = self._setup_turn(
                routing,
                channel=channel,
                target=target,
                session_turn_fence_token=session_turn_fence_token,
            )

        await self._compact_session_if_needed(
            routing=routing,
            session_turn_fence_token=session_turn_fence_token,
        )
        with active_chat_phase("gateway_session_context"):
            history = await self._session_context.abuild_history(
                session_id=routing.session.id,
                channel=channel,
                target=target,
                recent_limit=self._history_limit,
                exclude_history_message_ids=exclude_history_message_ids,
                conversation_id=routing.conversation_id or None,
                thread_id=routing.thread_id or None,
                session_turn_fence_token=session_turn_fence_token,
            )
        with active_chat_phase("memory_retrieval"):
            turn_context = self._build_memory_context(
                routing,
                channel=channel,
                target=target,
                body=body,
                run_id=run_id,
                history=history,
                session_turn_fence_token=session_turn_fence_token,
            )
        setup_cost_route = classify_setup_cost_route(
            message=body,
            forced_tools=forced_tools,
            capability_category=capability_category,
            inbound_metadata=inbound_metadata,
        )
        typed_terminal_resolver = self._build_gtgs_terminal_resolver(
            typed_turn_intent=typed_turn_intent,
            session_turn_fence_token=session_turn_fence_token,
        )

        self._emit_agent_running_state(
            routing=routing,
            run_id=run_id,
            lifecycle_payload=lifecycle_payload,
            turn_context=turn_context,
            setup_cost_route=setup_cost_route,
            session_turn_fence_token=session_turn_fence_token,
        )

        try:
            with active_chat_phase("gateway_agent_dispatch"):
                response = await self._execute_agent(
                    routing,
                    channel=channel,
                    target=target,
                    body=body,
                    run_id=run_id,
                    lifecycle_payload=lifecycle_payload,
                    history=turn_context.history,
                    forced_tools=forced_tools,
                    capability_category=capability_category,
                    prior_transcript_available=turn_context.prior_transcript_available,
                    progress_callback=progress_callback,
                    approval_callback=approval_callback,
                    session_turn_fence_token=session_turn_fence_token,
                )

            if _response_is_pae_idle_tick_noop(response):
                return self._complete_suppressed_idle_tick(
                    routing=routing,
                    run_id=run_id,
                    response=response,
                    lifecycle_payload=lifecycle_payload,
                    typed_terminal_resolver=typed_terminal_resolver,
                    session_turn_fence_token=session_turn_fence_token,
                )

            with active_chat_phase("response_persistence"):
                outbound, outbound_record = self._build_outbound_and_persist(
                    routing,
                    run_id=run_id,
                    response=response,
                    memory_context_meta=turn_context.memory_context_meta,
                    memory_retrieval_meta=turn_context.memory_retrieval_meta,
                    session_turn_fence_token=session_turn_fence_token,
                )

            with active_chat_phase("memory_write"):
                self._write_turn_memory(
                    routing,
                    channel=channel,
                    target=target,
                    body=body,
                    run_id=run_id,
                    outbound=outbound,
                    session_turn_fence_token=session_turn_fence_token,
                )

            input_tokens, output_tokens = self._usage_totals_from_response(response)
            self._finish_run_record(
                run_id,
                status="completed",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

            with active_chat_phase("cli_render_delivery"):
                self._deliver_and_complete(
                    routing,
                    channel=channel,
                    target=target,
                    run_id=run_id,
                    lifecycle_payload=lifecycle_payload,
                    response=response,
                    outbound=outbound,
                    outbound_record=outbound_record,
                    deliver=deliver,
                    typed_terminal_resolver=typed_terminal_resolver,
                    session_turn_lease_owner=session_turn_lease_owner,
                    session_turn_fence_token=session_turn_fence_token,
                )
            return outbound

        except RuntimeSessionTurnFenceError:
            raise
        except Exception as exc:
            self._finish_run_record(run_id, status="failed")
            with active_chat_phase("terminal_event"):
                self._lifecycle_ops.emit_terminal_run_state(
                    session_id=routing.session.id,
                    run_id=run_id,
                    legacy_state=RUN_STATE_FAILED,
                    current_step="turn.failed",
                    payload=self._lifecycle_ops.corr_payload(
                        normalized_request_id=routing.normalized_request_id,
                        lifecycle_payload=lifecycle_payload,
                        extra={"error": str(exc)},
                    ),
                    conversation_id=routing.conversation_id or None,
                    thread_id=routing.thread_id or None,
                    attach_id=routing.attach_id or None,
                    typed_terminal_resolver=typed_terminal_resolver,
                    session_turn_fence_token=session_turn_fence_token,
                )
            self._logger.warning(
                "gateway turn failed channel=%s target=%s session_id=%s run_id=%s request_id=%s error=%s",
                channel,
                target,
                routing.session.id,
                run_id,
                routing.normalized_request_id,
                exc,
            )
            raise
