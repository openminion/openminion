from types import SimpleNamespace
from typing import Any

from openminion.modules.brain.constants import (
    BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
    BRAIN_ACT_PROFILE_CODING,
    BRAIN_ACT_PROFILE_GENERAL,
    BRAIN_DECISION_ROUTE_ACT,
    BRAIN_STATE_ERROR,
    BRAIN_STATE_WAITING_USER,
    RESPOND_KIND_ASSISTANT,
    RESPOND_KIND_POLICY_CONFIRMATION_PROMPT,
)
from openminion.modules.brain.config import (
    ADAPTIVE_MAX_ITERATIONS,
    ADAPTIVE_MAX_TOOL_CALLS,
    TOOL_SCHEMA_SHORTLISTING_ENABLED,
)
from openminion.modules.brain.config import ADAPTIVE_BUDGET_HARD_CAP
from openminion.modules.brain.schemas import AdaptiveBudgetConfig
from openminion.modules.brain.diagnostics.transitions import transition
from openminion.modules.brain.execution.loop_contracts import (
    ExecutionContext,
    ExecutionResult,
)
from openminion.modules.brain.execution.preflight import (
    ModePreparation,
    ValidationResult,
)
from openminion.modules.brain.loop.tools.direct_tool import (
    _restore_direct_tool_specs_after_shortlist,
)
from openminion.modules.brain.schemas import (
    build_intent_execution_states,
)
from openminion.modules.brain.loop.tools import (
    ADAPTIVE_CLOSURE_ENGINE_SINGLE_PASS,
    AdaptiveToolLoopOutcome,
    AdaptiveToolLoopProfile,
    AdaptiveToolLoopRuntimeUnavailableError,
    AdaptiveToolLoopState,
    DefaultAdaptiveToolLoopLLMRuntime,
    build_loop_thinking_metadata,
    build_runtime_tool_specs,
    resolve_loop_model,
    run_adaptive_tool_loop,
    should_shortlist_tool_schemas,
    shortlist_tool_schemas,
)
from openminion.modules.brain.loop.tools.budget import _debit_llm_usage
from openminion.modules.brain.loop.tools.budget_extension import (
    consume_approved_extension,
)
from openminion.modules.llm.schemas import Message
from openminion.modules.memory.runtime.consolidation import (
    apply_memory_consolidation_decisions,
)
from openminion.modules.brain.runtime.memory import (
    stage_declared_goal,
    stage_goal_revision,
    stage_meta_rule_preference,
)
from openminion.modules.tool.contracts.model_ids import (
    MODEL_CODE_PATCH,
    MODEL_FILE_EDIT,
    MODEL_FILE_WRITE,
)

from ..services import runner_from_context

from .allowed_tools import (
    ACT_ADAPTIVE_ALLOWED_TOOLS,
    _memory_consolidation_profile_overrides,
    _watch_profile_overrides,
    _with_decompose_tool_spec,
    _with_general_decompose_allowed_tools,
)
from .context import (
    _AdaptiveLoopContextAdapter,
    _adaptive_loop_metadata,
    _direct_tool_turn_context,
)
from .tool_scope import (
    _CONTROL_TOOL_OPT_OUT_TOKENS,
    _adaptive_public_attr,
    _explicit_tool_opt_out_tokens,
    _public_act_label,
    _public_act_tag,
    _with_direct_tool_requested_allowed_tools,
    _without_control_tool_names,
    _without_explicit_tool_opt_outs,
)
from .termination import (
    _build_error_result,
    _extract_failure_memories_for_outcome,
    effective_soft_cap,
)
from ..strategies.coding import execute_coding_profile, prepare_coding_profile

_SEEDED_REPLAY_ARTIFACT_MUTATION_TOOLS = frozenset(
    {
        MODEL_CODE_PATCH,
        MODEL_FILE_EDIT,
        MODEL_FILE_WRITE,
    }
)
_CONTROL_RESTRICTED_REASON_CODES = frozenset(
    {
        "confirmation_replay",
        "confirmation_replay_recovery",
        "research_iteration_fallback",
    }
)


from .finalization import ActLoopFinalizationMixin  # noqa: E402
from .seeded import ActLoopSeededMixin  # noqa: E402


class ActLoopMode(ActLoopSeededMixin, ActLoopFinalizationMixin):
    mode_name = BRAIN_INTERNAL_MODE_ACT_ADAPTIVE
    mode_description = (
        "execute work now through the shared same-turn act loop. Use for "
        "general local action work where the next tool may depend on prior "
        "tool output."
    )
    mode_category = "action"
    has_prepare = True
    has_validate = True
    has_resume = False
    priority_hint = 51
    mode_thinking_policy = {
        "default_reasoning_profile": "minimal",
        "allowed_reasoning_profiles": ("minimal", "detailed"),
        "allow_request_override": True,
    }
    decision_payload_fields: dict[str, Any] = {}
    default_config = {
        "max_depth": 1,
        "max_adaptive_iterations": ADAPTIVE_MAX_ITERATIONS,
        "max_adaptive_tool_calls_per_loop": ADAPTIVE_MAX_TOOL_CALLS,
        "tool_schema_shortlisting_enabled": TOOL_SCHEMA_SHORTLISTING_ENABLED,
    }

    @staticmethod
    def _result_from_needs_user(
        ctx: ExecutionContext,
        *,
        outcome: AdaptiveToolLoopOutcome,
    ) -> ExecutionResult:
        message = (
            str(getattr(ctx.state, "post_action_user_message", "") or "").strip()
            or getattr(outcome.action_result, "summary", "")
            or "Approval required."
        )
        # PCHC-2: route on the typed pending-confirmation state field, not
        # on confirmation prose content. This keeps policy-confirmation
        # prompts audit-visible without turning them into assistant history.
        needs_user_kind = RESPOND_KIND_ASSISTANT
        if (
            getattr(ctx.state, "pending_confirmation_command", None) is not None
            and str(getattr(ctx.state, "post_action_user_message", "") or "").strip()
        ):
            needs_user_kind = RESPOND_KIND_POLICY_CONFIRMATION_PROMPT
        return ExecutionResult(
            status=BRAIN_STATE_WAITING_USER,
            working_state=ctx.state,
            message=message,
            action_result=outcome.action_result,
            kind=needs_user_kind,
        )

    def __init__(self) -> None:
        self._max_iterations = ADAPTIVE_MAX_ITERATIONS
        self._max_tool_calls_per_loop = ADAPTIVE_MAX_TOOL_CALLS
        self._tool_schema_shortlisting_enabled = TOOL_SCHEMA_SHORTLISTING_ENABLED
        self._reflection_policy: str = "never"

    def apply_mode_config(self, *, config, runner, profile) -> None:
        del profile
        max_iterations = getattr(config, "max_adaptive_iterations", None)
        max_tool_calls = getattr(config, "max_adaptive_tool_calls_per_loop", None)
        reflection_policy = getattr(config, "adaptive_reflection_policy", None)
        tss_enabled = getattr(config, "tool_schema_shortlisting_enabled", None)
        if isinstance(config, dict):
            if max_iterations is None:
                max_iterations = config.get("max_adaptive_iterations")
            if max_tool_calls is None:
                max_tool_calls = config.get("max_adaptive_tool_calls_per_loop")
            if reflection_policy is None:
                reflection_policy = config.get("adaptive_reflection_policy")
            if tss_enabled is None:
                tss_enabled = config.get("tool_schema_shortlisting_enabled")
        # Fall back to runtime-level RunnerOptions if per-agent mode config
        # didn't set it (runtime > code default, per-agent > runtime)
        if tss_enabled is None:
            runner_options = getattr(runner, "options", None)
            if runner_options is not None:
                runtime_tss = getattr(
                    runner_options, "tool_schema_shortlisting_enabled", None
                )
                if runtime_tss is not None:
                    tss_enabled = runtime_tss
        self._max_iterations = max(1, int(max_iterations or ADAPTIVE_MAX_ITERATIONS))
        self._max_tool_calls_per_loop = max(
            1,
            int(max_tool_calls or ADAPTIVE_MAX_TOOL_CALLS),
        )
        if tss_enabled is not None:
            self._tool_schema_shortlisting_enabled = bool(tss_enabled)
        if reflection_policy in ("never", "always", "anomaly"):
            self._reflection_policy = reflection_policy

    def prepare(
        self,
        ctx: ExecutionContext,
        *,
        emit_status_updates: bool = False,
    ) -> ModePreparation:
        if self._act_profile(ctx) == BRAIN_ACT_PROFILE_CODING:
            prepare_coding = _adaptive_public_attr(
                "prepare_coding_profile", prepare_coding_profile
            )
            return prepare_coding(
                ctx,
                emit_status_updates=emit_status_updates,
            )
        if getattr(ctx.decision, "_seeded_commands", None):
            ctx.emit_status(
                source_phase="act_adaptive.prepare",
                detail_text=f"{_public_act_tag()} started",
                mode=BRAIN_DECISION_ROUTE_ACT,
                mode_state="prepare",
                payload={
                    "act.profile": BRAIN_ACT_PROFILE_GENERAL,
                    "act.seeded_command_count": len(
                        list(getattr(ctx.decision, "_seeded_commands", []) or [])
                    ),
                },
            )
            return ModePreparation(
                mode_result=None, consume_user_input_for_command=False
            )
        del emit_status_updates
        try:
            DefaultAdaptiveToolLoopLLMRuntime.from_adapter(ctx.llm_adapter)
        except AdaptiveToolLoopRuntimeUnavailableError as exc:
            ctx.emit_status(
                source_phase="act_adaptive.prepare",
                detail_text=f"{_public_act_tag()} raw LLM runtime unavailable",
                mode=BRAIN_DECISION_ROUTE_ACT,
                mode_state="prepare_failed",
                payload={
                    "act.profile": BRAIN_ACT_PROFILE_GENERAL,
                },
            )
            return ModePreparation(
                mode_result=ExecutionResult(
                    status=BRAIN_STATE_ERROR,
                    working_state=ctx.state,
                    message=str(exc),
                    action_result=_build_error_result(
                        str(exc), "act_adaptive_runtime_unavailable"
                    ),
                ),
                consume_user_input_for_command=False,
            )

        ctx.emit_status(
            source_phase="act_adaptive.prepare",
            detail_text=f"{_public_act_tag()} started",
            mode=BRAIN_DECISION_ROUTE_ACT,
            mode_state="prepare",
            payload={
                "act.profile": BRAIN_ACT_PROFILE_GENERAL,
                "act.allowed_tools": sorted(ACT_ADAPTIVE_ALLOWED_TOOLS),
            },
        )
        return ModePreparation(mode_result=None, consume_user_input_for_command=False)

    def validate(
        self,
        ctx: ExecutionContext,
        *,
        preparation: ModePreparation | None = None,
    ) -> ValidationResult | None:
        del preparation
        return None

    def execute(self, ctx: ExecutionContext) -> ExecutionResult:
        if self._act_profile(ctx) == BRAIN_ACT_PROFILE_CODING:
            execute_coding = _adaptive_public_attr(
                "execute_coding_profile", execute_coding_profile
            )
            return execute_coding(ctx)
        if getattr(ctx.decision, "_seeded_commands", None):
            return self._execute_seeded_commands(ctx)
        try:
            runtime = DefaultAdaptiveToolLoopLLMRuntime.from_adapter(ctx.llm_adapter)
        except AdaptiveToolLoopRuntimeUnavailableError as exc:
            return ExecutionResult(
                status=BRAIN_STATE_ERROR,
                working_state=ctx.state,
                message=str(exc),
                action_result=_build_error_result(
                    str(exc), "act_adaptive_runtime_unavailable"
                ),
            )
        if not getattr(ctx.state, "intent_execution_states", []) and getattr(
            ctx.state, "decision_sub_intent_refs", []
        ):
            ctx.state.intent_execution_states = build_intent_execution_states(
                list(getattr(ctx.state, "decision_sub_intent_refs", []) or []),
                existing=[],
            )

        runner = runner_from_context(ctx)
        consolidation_overrides = _memory_consolidation_profile_overrides(ctx)
        watch_overrides = (
            None
            if consolidation_overrides is not None
            else _watch_profile_overrides(ctx)
        )
        effective_allowed_tools = (
            consolidation_overrides["allowed_tools"]
            if consolidation_overrides is not None
            else (
                watch_overrides["allowed_tools"]
                if watch_overrides is not None
                else ACT_ADAPTIVE_ALLOWED_TOOLS
            )
        )
        decision_reason_code = str(
            getattr(ctx.decision, "reason_code", "") or ""
        ).strip()
        explicit_tool_opt_outs = _explicit_tool_opt_out_tokens(ctx)
        control_tool_opt_out = bool(
            explicit_tool_opt_outs & _CONTROL_TOOL_OPT_OUT_TOKENS
        )
        if decision_reason_code in _CONTROL_RESTRICTED_REASON_CODES:
            effective_allowed_tools = _without_control_tool_names(
                frozenset(effective_allowed_tools)
            )
        if explicit_tool_opt_outs:
            effective_allowed_tools = _without_explicit_tool_opt_outs(
                frozenset(effective_allowed_tools),
                opt_out_tokens=explicit_tool_opt_outs,
            )
        model = resolve_loop_model(ctx)
        messages = []
        seeded_replay_without_new_input = bool(
            self._seeded_continue_stays_autonomous(ctx)
            and not str(ctx.user_input or "").strip()
        )
        if ctx.user_input:
            messages.append(Message(role="user", content=ctx.user_input))
        elif getattr(ctx.state, "goal", "") and not seeded_replay_without_new_input:
            messages.append(Message(role="user", content=str(ctx.state.goal or "")))
        seed_response = getattr(ctx.decision, "_entry_response", None)
        direct_tool_turn = _direct_tool_turn_context(
            ctx=ctx,
            seed_response=seed_response,
        )
        effective_allowed_tools = _with_direct_tool_requested_allowed_tools(
            frozenset(effective_allowed_tools),
            direct_tool_turn,
        )
        full_tool_specs = build_runtime_tool_specs(
            runner,
            allowed_tools=effective_allowed_tools,
        )

        # compute the per-turn soft cap from typed Decision fields
        _aib_config = getattr(ctx.options, "adaptive_budget_config", None)
        if _aib_config is None:
            _aib_config = AdaptiveBudgetConfig()
        _scaled_cap_base = (
            int(consolidation_overrides["max_iterations"])
            if consolidation_overrides is not None
            else (
                int(watch_overrides["max_iterations"])
                if watch_overrides is not None
                else self._max_iterations
            )
        )
        _aib_scaled_cap = effective_soft_cap(ctx.decision, _aib_config)
        _effective_max_iterations = min(
            max(_scaled_cap_base, _aib_scaled_cap),
            ADAPTIVE_BUDGET_HARD_CAP,
        )
        _approved_extension = consume_approved_extension(state=ctx.state)
        if isinstance(_approved_extension, dict):
            try:
                _approved_target_cap = int(
                    _approved_extension.get("target_cap", 0) or 0
                )
            except (TypeError, ValueError):
                _approved_target_cap = 0
            if _approved_target_cap > 0:
                _effective_max_iterations = min(
                    max(_effective_max_iterations, _approved_target_cap),
                    ADAPTIVE_BUDGET_HARD_CAP,
                )

        profile_name = (
            "memory_consolidation_v1"
            if consolidation_overrides is not None
            else (
                (
                    "watch_action_v1"
                    if watch_overrides is not None
                    and str(watch_overrides.get("turn_kind", "") or "") == "action"
                    else "watch_check_v1"
                )
                if watch_overrides is not None
                else "general_adaptive_v1"
            )
        )
        effective_allowed_tools = _with_general_decompose_allowed_tools(
            effective_allowed_tools,
            profile_name=profile_name,
            decision_reason_code=decision_reason_code,
        )
        if explicit_tool_opt_outs:
            effective_allowed_tools = _without_explicit_tool_opt_outs(
                frozenset(effective_allowed_tools),
                opt_out_tokens=explicit_tool_opt_outs,
            )

        profile = AdaptiveToolLoopProfile(
            profile_name=profile_name,
            mode_name=BRAIN_INTERNAL_MODE_ACT_ADAPTIVE,
            allowed_tools=effective_allowed_tools,
            allow_plan_tool=(
                decision_reason_code != "research_iteration_fallback"
                and not control_tool_opt_out
            ),
            provider_parallel_tool_capacity=(
                0
                if consolidation_overrides is not None
                else (1 if watch_overrides is not None else 2)
            ),
            max_iterations=_effective_max_iterations,
            max_tool_calls_per_loop=self._max_tool_calls_per_loop,
            reflection_policy=self._reflection_policy,  # type: ignore[arg-type]
            max_macro_corrections=2,
            macro_correction_cooldown=1,
            allow_llm_recovery_after_tool_failure=True,
            tool_choice="none" if consolidation_overrides is not None else "auto",
            llm_request_overrides={
                "metadata": _adaptive_loop_metadata(ctx, purpose="act")
            },
            final_closure_policy=ADAPTIVE_CLOSURE_ENGINE_SINGLE_PASS,
            adaptive_budget_config=_aib_config,
        )
        loop_ctx_adapter = _AdaptiveLoopContextAdapter(ctx)
        shortlisting_scratchpad: dict[str, Any] = {}
        requestable_tool_specs = None
        tool_specs = full_tool_specs
        runtime_tool_registry_available = (
            getattr(getattr(runner, "tool_api", None), "registry", None) is not None
        )
        if (
            runtime_tool_registry_available
            and self._tool_schema_shortlisting_enabled
            and decision_reason_code != "research_iteration_fallback"
            and should_shortlist_tool_schemas(
                profile_name=profile.profile_name,
                tool_specs=full_tool_specs,
            )
        ):
            shortlist_result = shortlist_tool_schemas(
                runtime=runtime,
                model=model,
                user_messages=messages,
                tool_specs=full_tool_specs,
                metadata=build_loop_thinking_metadata(
                    ctx,
                    purpose="tool_schema_shortlist",
                ),
            )
            if shortlist_result.llm_call_made:
                _debit_llm_usage(
                    loop_ctx_adapter,
                    SimpleNamespace(
                        usage=SimpleNamespace(
                            input_tokens=shortlist_result.input_tokens,
                            output_tokens=shortlist_result.output_tokens,
                        )
                    ),
                )
            shortlisting_scratchpad.update(shortlist_result.scratchpad_payload())
            shortlisting_scratchpad.update(
                {
                    "turn_progress_input_tokens_total": shortlist_result.input_tokens,
                    "turn_progress_output_tokens_total": shortlist_result.output_tokens,
                    "turn_progress_total_tokens_used": shortlist_result.total_tokens,
                }
            )
            tool_specs = list(shortlist_result.active_tool_specs)
            if shortlist_result.enabled:
                requestable_tool_specs = list(shortlist_result.requestable_tool_specs)
        if (
            str(profile.profile_name or "").strip() == "general_adaptive_v1"
            and decision_reason_code != "research_iteration_fallback"
            and not control_tool_opt_out
        ):
            tool_specs = _with_decompose_tool_spec(list(tool_specs))
        if direct_tool_turn is not None and requestable_tool_specs is not None:
            tool_specs = _restore_direct_tool_specs_after_shortlist(
                loop_state=AdaptiveToolLoopState(
                    direct_tool_turn=direct_tool_turn,
                    scratchpad=shortlisting_scratchpad,
                ),
                active_tool_specs=list(tool_specs),
                requestable_tool_specs=requestable_tool_specs,
            )
        run_loop = _adaptive_public_attr(
            "run_adaptive_tool_loop", run_adaptive_tool_loop
        )
        outcome = run_loop(
            loop_ctx_adapter,
            profile=profile,
            runtime=runtime,
            model=model,
            initial_messages=messages,
            initial_state=AdaptiveToolLoopState(
                messages=list(messages),
                direct_tool_turn=direct_tool_turn,
                scratchpad=shortlisting_scratchpad,
            ),
            tool_specs=tool_specs,
            requestable_tool_specs=requestable_tool_specs,
            seed_response=seed_response,
            finalizer=lambda loop_outcome: self._finalize_success(
                ctx,
                loop_outcome=loop_outcome,
                runtime=runtime,
                model=model,
            ),
        )
        if outcome.mode_result is not None:
            return outcome.mode_result
        return self._result_from_outcome(ctx, outcome=outcome)


__all__ = [
    "ACT_ADAPTIVE_ALLOWED_TOOLS",
    "ActLoopMode",
    "_extract_failure_memories_for_outcome",
    "apply_memory_consolidation_decisions",
    "_public_act_label",
    "_public_act_tag",
    "stage_declared_goal",
    "stage_goal_revision",
    "stage_meta_rule_preference",
    "transition",
]
