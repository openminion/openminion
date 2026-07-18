import logging
from typing import Any, Callable

from openminion.base.config import OpenMinionConfig
from openminion.services.agent.memory import (
    MEMORY_POLICY_SNAPSHOT_VERSION,
)
from openminion.services.brain.constants import (
    OPENMINION_BRAIN_DECIDE_MODEL_ENV,
    OPENMINION_BRAIN_MAX_A2A_CALLS_ENV,
    OPENMINION_BRAIN_MAX_ELAPSED_MS_ENV,
    OPENMINION_BRAIN_MAX_TICKS_ENV,
    OPENMINION_BRAIN_MAX_TOOL_CALLS_ENV,
    OPENMINION_BRAIN_MAX_TOTAL_LLM_TOKENS_ENV,
    OPENMINION_BRAIN_MODEL_ENV,
    OPENMINION_BRAIN_PLAN_MODEL_ENV,
    OPENMINION_BRAIN_REFLECT_MODEL_ENV,
    OPENMINION_BRAIN_REFLECTION_ENABLED_ENV,
    OPENMINION_BRAIN_SUMMARIZE_MODEL_ENV,
)
from openminion.modules.brain.config import (
    ClarifyConfig,
    TOOL_SCHEMA_SHORTLISTING_ENABLED,
    _default_budgets as derive_default_budgets,
    _default_llm_profiles as derive_default_llm_profiles,
)
from openminion.modules.brain.runner import RunnerOptions
from openminion.modules.brain.schemas import AgentBudgets, LLMProfiles


OverrideValue = Callable[[str], str]


def _coerce_int(raw: str, *, default: int) -> int:
    raw = (raw or "").strip()
    if not raw:
        return int(default)
    try:
        return max(0, int(raw))
    except ValueError:
        return int(default)


def _coerce_bool(raw: str, *, default: bool) -> bool:
    raw = (raw or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def resolve_llm_profiles(
    config: OpenMinionConfig,
    *,
    override_value: OverrideValue,
) -> LLMProfiles:
    default_profiles = derive_default_llm_profiles(config)
    shared = override_value(OPENMINION_BRAIN_MODEL_ENV)
    decide = override_value(OPENMINION_BRAIN_DECIDE_MODEL_ENV)
    plan = override_value(OPENMINION_BRAIN_PLAN_MODEL_ENV)
    reflect = override_value(OPENMINION_BRAIN_REFLECT_MODEL_ENV)
    summarize = override_value(OPENMINION_BRAIN_SUMMARIZE_MODEL_ENV)
    return LLMProfiles(
        decide_model=str(decide or shared or default_profiles.decide_model),
        plan_model=str(plan or shared or default_profiles.plan_model),
        act_model=default_profiles.act_model,
        reflect_model=str(reflect or shared or default_profiles.reflect_model),
        summarize_model=str(summarize or shared or default_profiles.summarize_model),
    )


def resolve_agent_budgets(
    config: OpenMinionConfig,
    *,
    override_value: OverrideValue,
) -> AgentBudgets:
    default_budgets = derive_default_budgets(config)
    runtime_session_token_budget = int(
        getattr(
            getattr(config, "runtime", object()),
            "session_context_token_budget",
            0,
        )
        or 0
    )
    floor_total_llm_tokens = int(default_budgets.max_total_llm_tokens)
    if runtime_session_token_budget <= 0:
        floor_total_llm_tokens = max(floor_total_llm_tokens, 100000)

    return AgentBudgets(
        max_ticks_per_user_turn=max(
            1,
            _coerce_int(
                override_value(OPENMINION_BRAIN_MAX_TICKS_ENV),
                default=int(default_budgets.max_ticks_per_user_turn),
            ),
        ),
        max_tool_calls=max(
            0,
            _coerce_int(
                override_value(OPENMINION_BRAIN_MAX_TOOL_CALLS_ENV),
                default=int(default_budgets.max_tool_calls),
            ),
        ),
        max_a2a_calls=max(
            0,
            _coerce_int(
                override_value(OPENMINION_BRAIN_MAX_A2A_CALLS_ENV),
                default=int(default_budgets.max_a2a_calls),
            ),
        ),
        max_total_llm_tokens=max(
            1,
            _coerce_int(
                override_value(OPENMINION_BRAIN_MAX_TOTAL_LLM_TOKENS_ENV),
                default=floor_total_llm_tokens,
            ),
        ),
        max_elapsed_ms=max(
            1000,
            _coerce_int(
                override_value(OPENMINION_BRAIN_MAX_ELAPSED_MS_ENV),
                default=int(default_budgets.max_elapsed_ms),
            ),
        ),
    )


def resolve_runner_options(
    config: OpenMinionConfig,
    *,
    brain_config: Any | None,
    override_value: OverrideValue,
    logger: logging.Logger,
) -> RunnerOptions:
    plan_auto_scale_max_llm_calls = max(
        1,
        _coerce_int(
            override_value("OPENMINION_PLAN_AUTO_SCALE_MAX_LLM_CALLS"),
            default=128,
        ),
    )
    plan_auto_scale_max_ticks = max(
        1,
        _coerce_int(
            override_value("OPENMINION_PLAN_AUTO_SCALE_MAX_TICKS"),
            default=128,
        ),
    )
    plan_auto_scale_max_tokens = max(
        1000,
        _coerce_int(
            override_value("OPENMINION_PLAN_AUTO_SCALE_MAX_TOKENS"),
            default=500_000,
        ),
    )

    import openminion.services.brain.service as _bridge_module
    try:
        memory_policy_snapshot = _bridge_module.build_memory_policy_snapshot(
            config=config
        )
    except Exception as exc:  # noqa: BLE001
        memory_policy_snapshot = {
            "policy_source": "runtime.config",
            "policy_version": MEMORY_POLICY_SNAPSHOT_VERSION,
            "policy_error": f"policy_unavailable:{type(exc).__name__}",
        }
        logger.warning(
            "memory policy snapshot unavailable: %s",
            exc,
        )

    runtime_tss_enabled = getattr(
        getattr(getattr(config, "runtime", object()), "brain", None),
        "tool_schema_shortlisting_enabled",
        None,
    )

    options = RunnerOptions(
        max_retries_per_step=2,
        max_replans=8,
        plan_auto_scale_max_llm_calls=plan_auto_scale_max_llm_calls,
        plan_auto_scale_max_ticks=plan_auto_scale_max_ticks,
        plan_auto_scale_max_tokens=plan_auto_scale_max_tokens,
        reflection_enabled=_coerce_bool(
            override_value(OPENMINION_BRAIN_REFLECTION_ENABLED_ENV),
            default=False,
        ),
        idempotency_enabled=False,
        metactl_enabled=False,
        failure_strategy="halt",
        clarify_config=(
            brain_config.clarify if brain_config is not None else ClarifyConfig()
        ),
        request_handoff_enabled=bool(getattr(getattr(brain_config, "request_handoff", None), "enabled", False)),
        complex_request_plan_policy=str(
            getattr(
                getattr(config, "runtime", object()),
                "complex_request_plan_policy",
                "balanced",
            )
        ),
        memory_policy_snapshot=memory_policy_snapshot,
        skill_selection_strategy=str(
            getattr(brain_config, "skill_selection_strategy", "llm") or "llm"
        ),
        tool_schema_shortlisting_enabled=(
            bool(runtime_tss_enabled)
            if runtime_tss_enabled is not None
            else TOOL_SCHEMA_SHORTLISTING_ENABLED
        ),
    )
    aib_config = (
        brain_config.adaptive_budget.model_copy(deep=True)
        if brain_config is not None
        and getattr(brain_config, "adaptive_budget", None) is not None
        else None
    )
    if aib_config is not None:
        options.adaptive_budget_config = aib_config
    return options


__all__ = [
    "OverrideValue",
    "resolve_agent_budgets",
    "resolve_llm_profiles",
    "resolve_runner_options",
]
