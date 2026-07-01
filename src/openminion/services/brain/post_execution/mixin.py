import json
import time
import uuid
from typing import Any, Callable

from openminion.base.config.action_policy import (
    ACTION_POLICY_SESSION_OVERRIDE_KEY,
    normalize_action_policy_mode_override,
)
from openminion.base.types import AgentResponse, Message
from openminion.modules.brain.runner import BrainRunner
from openminion.modules.brain.diagnostics.status import (
    PhaseStatus,
    normalize_phase_status,
)
from openminion.services.security.policy import (
    SecurityPolicyContext,
    ToolBudgetState,
    default_internal_actor,
)
from openminion.services.security.blast_radius.wiring import (
    SEAM_BRAIN_RUNTIME_TOOL_API,
    build_default_composition_boundary_adapter,
)
from openminion.services.security.tool_execution import (
    build_execution_boundary_policy_adapter,
)


def _emit_prep_status(
    callback: Callable[[PhaseStatus], None] | None,
    *,
    trace_id: str,
    detail_text: str,
) -> None:
    if callback is None:
        return
    try:
        status = normalize_phase_status(
            trace_id=trace_id,
            source_phase="DECIDE",
            detail_text=detail_text,
        )
        callback(status)
    except Exception:  # noqa: BLE001 — best-effort telemetry; must not break turn prep
        return


class BrainBridgeTurnMixin:
    async def _prepare_turn(
        self,
        *,
        message: Message,
        history: list[Message],
        forced_tools: list[str] | None,
        capability_category: str | None,
        brain_session_id: str,
        progress_callback: Callable[[PhaseStatus], None] | None = None,
    ) -> tuple[BrainRunner, str, str | None, str, float]:
        prep_trace_id = f"prep-{uuid.uuid4().hex[:12]}"
        _emit_prep_status(
            progress_callback,
            trace_id=prep_trace_id,
            detail_text="Preparing turn...",
        )
        self._refresh_prep_identity_state()
        session_id = brain_session_id
        request_id = message.metadata.get("request_id")
        runner = self._get_runner()
        _emit_prep_status(
            progress_callback,
            trace_id=prep_trace_id,
            detail_text="Loading memory context...",
        )
        runtime_system_prompt, gateway_system_context = self._prepare_runtime_contexts(
            runner=runner,
            session_id=session_id,
            message=message,
            history=history,
        )

        turn_start_time = time.time()
        turn_id = f"{session_id}_{int(turn_start_time * 1000)}"
        self._bind_turn_runtime_context(
            runner=runner,
            session_id=session_id,
            turn_id=turn_id,
            system_prompt=runtime_system_prompt,
        )

        _emit_prep_status(
            progress_callback,
            trace_id=prep_trace_id,
            detail_text="Loading session history...",
        )
        self._hydrate_runner_session_context(
            runner=runner,
            session_id=session_id,
            history=history,
            system_prompt=runtime_system_prompt,
        )
        self._reset_state_for_new_input(
            runner=runner,
            session_id=session_id,
            user_input=message.body,
        )
        self._inject_gateway_system_context(
            runner=runner,
            session_id=session_id,
            gateway_system_context=gateway_system_context,
        )
        self._inject_resume_task_hints(
            runner=runner,
            session_id=session_id,
            inbound_metadata=dict(message.metadata or {}),
        )

        self._bind_tool_policy_adapter(
            runner=runner,
            message=message,
            session_id=session_id,
            request_id=request_id,
        )

        if self._telemetryctl:
            await self._telemetryctl.emit_tick(session_id, turn_id, 0)

        return runner, session_id, request_id, turn_id, turn_start_time

    def _refresh_prep_identity_state(self) -> None:
        self._last_identity_snippet = None
        refresh_identity_state = getattr(self, "_refresh_identity_runtime_state", None)
        if callable(refresh_identity_state):
            refresh_identity_state()

    def _prepare_runtime_contexts(
        self,
        *,
        runner: BrainRunner,
        session_id: str,
        message: Message,
        history: list[Message],
    ) -> tuple[str, str]:
        runtime_system_prompt = self._runtime_system_prompt(user_message=message.body)
        gateway_system_context = self._collect_system_history_context(history=history)
        runtime_memory_context = self._build_runtime_memory_context(
            runner=runner,
            session_id=session_id,
            user_message=message.body,
            history=history,
        )
        if runtime_memory_context:
            gateway_system_context = "\n\n".join(
                item
                for item in (gateway_system_context, runtime_memory_context)
                if item
            )
        if gateway_system_context:
            try:
                runner._pending_gateway_system_context = gateway_system_context
            except Exception:  # noqa: BLE001
                pass
        return self._prepare_runtime_system_prompt(
            runner=runner,
            session_id=session_id,
            message=message,
            history=history,
            runtime_system_prompt=runtime_system_prompt,
        ), gateway_system_context

    def _prepare_runtime_system_prompt(
        self,
        *,
        runner: BrainRunner,
        session_id: str,
        message: Message,
        history: list[Message],
        runtime_system_prompt: str,
    ) -> str:
        inject_identity_system_prompt = getattr(
            self, "_inject_identity_system_prompt", None
        )
        if callable(inject_identity_system_prompt):
            runtime_system_prompt = inject_identity_system_prompt(
                system_prompt=runtime_system_prompt,
                inbound_metadata=dict(message.metadata or {}),
            )
        return self._append_runtime_grounding_block(
            runner=runner,
            session_id=session_id,
            history=history,
            inbound_metadata=dict(message.metadata or {}),
            system_prompt=runtime_system_prompt,
        )

    def _bind_turn_runtime_context(
        self,
        *,
        runner: BrainRunner,
        session_id: str,
        turn_id: str,
        system_prompt: str,
    ) -> None:
        self._apply_runtime_system_prompt_override(
            runner=runner,
            system_prompt=system_prompt,
        )
        if (
            hasattr(self, "_llm_wrapper")
            and self._llm_wrapper
            and hasattr(self._llm_wrapper, "_set_context")
        ):
            self._llm_wrapper._set_context(session_id, turn_id)
        for api_name in ("session_api", "memory_api", "skill_api", "compress_api"):
            api = getattr(runner, api_name, None)
            setter = getattr(api, "set_telemetry_context", None)
            if callable(setter):
                setter(session_id=session_id, turn_id=turn_id)

    def _bind_tool_policy_adapter(
        self,
        *,
        runner: BrainRunner,
        message: Message,
        session_id: str,
        request_id: str | None,
    ) -> None:
        if self._security_policy is None or not hasattr(runner, "tool_api"):
            return
        tool_policy_lookup = None
        if self._tools is not None and hasattr(self._tools, "policy_for"):
            tool_policy_lookup = self._tools.policy_for  # type: ignore[assignment]
        tool_policy_adapter = build_execution_boundary_policy_adapter(
            policy=self._security_policy,
            actor=default_internal_actor(self._identity_agent_id),
            context=SecurityPolicyContext(
                channel=str(message.channel or "brain"),
                target="tool-runtime",
                session_id=str(session_id),
                run_id=str(request_id or ""),
            ),
            tool_policy_lookup=tool_policy_lookup,
            budget_state=ToolBudgetState(),
            blast_radius_adapter=build_default_composition_boundary_adapter(
                seam_id=SEAM_BRAIN_RUNTIME_TOOL_API,
            ),
        )
        tool_api = getattr(runner, "tool_api", None)
        if tool_api is not None and hasattr(tool_api, "policy_adapter"):
            tool_api.policy_adapter = tool_policy_adapter

    def _execute_turn(
        self,
        *,
        runner: BrainRunner,
        session_id: str,
        request_id: str | None,
        message: Message,
        forced_tools: list[str] | None,
        capability_category: str | None,
        progress_callback=None,
        approval_callback=None,
    ) -> Any:
        # cron-scheduled idle ticks arrive with a `pae_idle_tick`
        metadata_source = getattr(message, "metadata", {}) or {}
        permission_mode = str(metadata_source.get("permission_mode", "") or "").strip()
        action_policy_mode = normalize_action_policy_mode_override(
            metadata_source.get(ACTION_POLICY_SESSION_OVERRIDE_KEY)
        )
        permission_overrides_raw = str(
            metadata_source.get("permission_overrides", "") or ""
        ).strip()
        permission_overrides: dict[str, str] = {}
        if permission_overrides_raw:
            try:
                parsed = json.loads(permission_overrides_raw)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                permission_overrides = {
                    str(tool or "").strip().lower(): str(mode or "").strip().lower()
                    for tool, mode in parsed.items()
                    if str(tool or "").strip()
                }
        setattr(runner, "_pending_permission_mode", permission_mode or "default")
        setattr(runner, "_pending_permission_overrides", permission_overrides)
        setattr(
            runner,
            "_pending_session_action_policy_mode_override",
            action_policy_mode,
        )
        is_pae_idle_tick = (
            str(metadata_source.get("pae_idle_tick", "")).strip().lower() == "true"
        )
        if is_pae_idle_tick:
            from openminion.modules.brain.loop.continuation import (
                run_with_autonomous_continuation,
            )

            options = getattr(runner, "options", None)
            return run_with_autonomous_continuation(
                runner,
                session_id=session_id,
                user_input=None,
                trace_id=request_id,
                forced_tools=forced_tools,
                capability_category=capability_category,
                max_per_plan=int(
                    getattr(
                        options,
                        "autonomous_continuation_max_per_plan",
                        10,
                    )
                    or 10
                ),
                max_per_session=int(
                    getattr(
                        options,
                        "autonomous_continuation_max_per_session",
                        20,
                    )
                    or 20
                ),
                progress_callback=progress_callback,
                approval_callback=approval_callback,
                initial_trigger="idle_tick",
            )
        options = getattr(runner, "options", None)
        ctgp_enabled = bool(getattr(options, "autonomous_continuation_enabled", True))
        if ctgp_enabled:
            from openminion.modules.brain.loop.continuation import (
                run_with_autonomous_continuation,
            )

            return run_with_autonomous_continuation(
                runner,
                session_id=session_id,
                user_input=message.body,
                trace_id=request_id,
                forced_tools=forced_tools,
                capability_category=capability_category,
                max_per_plan=int(
                    getattr(
                        options,
                        "autonomous_continuation_max_per_plan",
                        10,
                    )
                    or 10
                ),
                max_per_session=int(
                    getattr(
                        options,
                        "autonomous_continuation_max_per_session",
                        20,
                    )
                    or 20
                ),
                progress_callback=progress_callback,
                approval_callback=approval_callback,
            )
        return runner.run(
            session_id=session_id,
            user_input=message.body,
            trace_id=request_id,
            forced_tools=forced_tools,
            capability_category=capability_category,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
        )

    async def run_turn(
        self,
        message: Message,
        history: list[Message] | None = None,
        forced_tools: list[str] | None = None,
        capability_category: str | None = None,
        progress_callback=None,
        approval_callback=None,
    ) -> AgentResponse:
        runtime_session_id, brain_session_id = self._resolve_turn_session_ids(
            message=message
        )
        resolved_capability_category = str(capability_category or "").strip() or None
        self._logger.info(
            "BrainBridgeService handling turn for runtime_session=%s brain_session=%s forced_tools=%s capability_category=%s",
            runtime_session_id,
            brain_session_id,
            forced_tools,
            resolved_capability_category,
        )

        (
            runner,
            session_id,
            request_id,
            turn_id,
            turn_start_time,
        ) = await self._prepare_turn(
            message=message,
            history=history or [],
            forced_tools=forced_tools,
            capability_category=resolved_capability_category,
            brain_session_id=brain_session_id,
            progress_callback=progress_callback,
        )

        step_out = self._execute_turn(
            runner=runner,
            session_id=session_id,
            request_id=request_id,
            message=message,
            forced_tools=forced_tools,
            capability_category=resolved_capability_category,
            progress_callback=progress_callback,
            approval_callback=approval_callback,
        )

        return await self._postprocess_turn(
            runner=runner,
            step_out=step_out,
            message=message,
            history=history,
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            turn_start_time=turn_start_time,
        )


from .context import (  # noqa: E402
    _append_runtime_grounding_block,
    _apply_runtime_system_prompt_override,
    _build_runtime_memory_context,
    _collect_llm_call_counts_by_purpose,
    _collect_system_history_context,
    _hydrate_runner_session_context,
    _inject_gateway_system_context,
    _inject_resume_task_hints,
    _is_state_machine_error_text,
    _normalize_llm_purpose,
    _normalize_turn_content,
    _pending_history_turns_to_hydrate,
    _prior_turn_context_hint,
    _pending_turn_context_for_prompt,
    _resolve_turn_session_ids,
    _runner_turn_signatures,
    _runtime_system_prompt,
    _turn_signature,
)
from .followup import (  # noqa: E402
    _build_tool_follow_up_history,
    _fallback_tool_follow_up_text,
    _finalize_tool_follow_up_text,
    _follow_up_after_tool,
    _looks_like_embedded_tool_call,
    _normalize_follow_up_response,
    _record_session_event,
    _should_replace_with_tool_fallback_text,
    _tool_result_from_action,
    _usage_payload_from_provider_response,
)
from .postprocess import (  # noqa: E402
    _active_mode_name_from_step,
    _apply_tool_result_postprocess,
    _attach_clarify_request_metadata,
    _attach_tool_result_metadata,
    _build_clarify_request_payload,
    _build_turn_response_metadata,
    _extract_memory_policy_metadata,
    _postprocess_turn,
    _resolve_command,
    _security_events_from_tool_results,
)
from .reset import (  # noqa: E402
    _apply_continuation_guard_reset,
    _apply_decision_state_reset,
    _apply_mission_reset_preview,
    _apply_open_questions_and_budget_reset,
    _apply_pending_confirmation_reset,
    _apply_plan_and_goal_reset,
    _apply_task_backed_reset,
    _base_turn_reset_state,
    _latest_working_state_inline,
    _mission_reset_preview,
    _reset_state_for_new_input,
    _turn_reset_preservation,
    _write_working_state_inline,
)

BrainBridgeTurnMixin._resolve_turn_session_ids = staticmethod(_resolve_turn_session_ids)
BrainBridgeTurnMixin._normalize_llm_purpose = staticmethod(_normalize_llm_purpose)
BrainBridgeTurnMixin._collect_llm_call_counts_by_purpose = staticmethod(
    _collect_llm_call_counts_by_purpose
)
BrainBridgeTurnMixin._append_runtime_grounding_block = _append_runtime_grounding_block
BrainBridgeTurnMixin._build_runtime_memory_context = _build_runtime_memory_context
BrainBridgeTurnMixin._inject_gateway_system_context = _inject_gateway_system_context
BrainBridgeTurnMixin._inject_resume_task_hints = _inject_resume_task_hints
BrainBridgeTurnMixin._pending_history_turns_to_hydrate = (
    _pending_history_turns_to_hydrate
)
BrainBridgeTurnMixin._prior_turn_context_hint = _prior_turn_context_hint
BrainBridgeTurnMixin._pending_turn_context_for_prompt = _pending_turn_context_for_prompt
BrainBridgeTurnMixin._hydrate_runner_session_context = _hydrate_runner_session_context
BrainBridgeTurnMixin._runtime_system_prompt = _runtime_system_prompt
BrainBridgeTurnMixin._collect_system_history_context = staticmethod(
    _collect_system_history_context
)
BrainBridgeTurnMixin._apply_runtime_system_prompt_override = staticmethod(
    _apply_runtime_system_prompt_override
)
BrainBridgeTurnMixin._runner_turn_signatures = _runner_turn_signatures
BrainBridgeTurnMixin._turn_signature = _turn_signature
BrainBridgeTurnMixin._normalize_turn_content = _normalize_turn_content
BrainBridgeTurnMixin._is_state_machine_error_text = staticmethod(
    _is_state_machine_error_text
)

BrainBridgeTurnMixin._looks_like_embedded_tool_call = staticmethod(
    _looks_like_embedded_tool_call
)
BrainBridgeTurnMixin._fallback_tool_follow_up_text = staticmethod(
    _fallback_tool_follow_up_text
)
BrainBridgeTurnMixin._should_replace_with_tool_fallback_text = staticmethod(
    _should_replace_with_tool_fallback_text
)
BrainBridgeTurnMixin._tool_result_from_action = staticmethod(_tool_result_from_action)
BrainBridgeTurnMixin._record_session_event = staticmethod(_record_session_event)
BrainBridgeTurnMixin._build_tool_follow_up_history = staticmethod(
    _build_tool_follow_up_history
)
BrainBridgeTurnMixin._normalize_follow_up_response = _normalize_follow_up_response
BrainBridgeTurnMixin._usage_payload_from_provider_response = staticmethod(
    _usage_payload_from_provider_response
)
BrainBridgeTurnMixin._finalize_tool_follow_up_text = staticmethod(
    _finalize_tool_follow_up_text
)
BrainBridgeTurnMixin._follow_up_after_tool = _follow_up_after_tool

BrainBridgeTurnMixin._resolve_command = staticmethod(_resolve_command)
BrainBridgeTurnMixin._build_clarify_request_payload = staticmethod(
    _build_clarify_request_payload
)
BrainBridgeTurnMixin._extract_memory_policy_metadata = staticmethod(
    _extract_memory_policy_metadata
)
BrainBridgeTurnMixin._active_mode_name_from_step = staticmethod(
    _active_mode_name_from_step
)
BrainBridgeTurnMixin._apply_tool_result_postprocess = _apply_tool_result_postprocess
BrainBridgeTurnMixin._build_turn_response_metadata = _build_turn_response_metadata
BrainBridgeTurnMixin._attach_clarify_request_metadata = staticmethod(
    _attach_clarify_request_metadata
)
BrainBridgeTurnMixin._security_events_from_tool_results = staticmethod(
    _security_events_from_tool_results
)
BrainBridgeTurnMixin._attach_tool_result_metadata = _attach_tool_result_metadata
BrainBridgeTurnMixin._postprocess_turn = _postprocess_turn

BrainBridgeTurnMixin._latest_working_state_inline = staticmethod(
    _latest_working_state_inline
)
BrainBridgeTurnMixin._write_working_state_inline = staticmethod(
    _write_working_state_inline
)
BrainBridgeTurnMixin._mission_reset_preview = staticmethod(_mission_reset_preview)
BrainBridgeTurnMixin._turn_reset_preservation = staticmethod(_turn_reset_preservation)
BrainBridgeTurnMixin._base_turn_reset_state = staticmethod(_base_turn_reset_state)
BrainBridgeTurnMixin._apply_pending_confirmation_reset = staticmethod(
    _apply_pending_confirmation_reset
)
BrainBridgeTurnMixin._apply_decision_state_reset = staticmethod(
    _apply_decision_state_reset
)
BrainBridgeTurnMixin._apply_continuation_guard_reset = staticmethod(
    _apply_continuation_guard_reset
)
BrainBridgeTurnMixin._apply_plan_and_goal_reset = staticmethod(
    _apply_plan_and_goal_reset
)
BrainBridgeTurnMixin._apply_open_questions_and_budget_reset = staticmethod(
    _apply_open_questions_and_budget_reset
)
BrainBridgeTurnMixin._apply_task_backed_reset = staticmethod(_apply_task_backed_reset)
BrainBridgeTurnMixin._apply_mission_reset_preview = staticmethod(
    _apply_mission_reset_preview
)
BrainBridgeTurnMixin._reset_state_for_new_input = _reset_state_for_new_input

__all__ = ["BrainBridgeTurnMixin"]
