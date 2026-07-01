from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from openminion.base.config import OpenMinionConfig
from openminion.base.config.core import resolve_default_agent_id

from .meta.schemas import MetaConfig as MetaRuntimeConfig, VerificationMode
from .schemas import (
    AdaptiveBudgetConfig,
    AgentBudgets,
    AgentDefaults,
    AgentProfile,
    AutoFactExtractionConfig,
    BudgetTelemetryConfig,
    BrainMode,
    ClarifyPolicy,
    LLMProfiles,
    OutcomeAttributionConfig,
    ProactiveAutonomousEntrypointConfig,
    SuccessMemoryConfig,
)
from .constants import (
    DEFAULT_CONFIG_FILENAMES,
    DEFAULT_INTEGRATED_CONFIG_SUBDIR,
)

from .act_profiles import (  # noqa: F401
    _ALLOWED_ACT_PROFILES,
    fixed_act_profile_from_context,
    fixed_act_profile_from_profile,
    normalize_default_act_profile,
)

_LOGGER = logging.getLogger(__name__)

ADAPTIVE_MAX_ITERATIONS = 24
ADAPTIVE_MAX_TOOL_CALLS = 32
TOOL_SCHEMA_SHORTLISTING_ENABLED = False
TOOL_SCHEMA_SHORTLIST_THRESHOLD = 8
TOOL_SCHEMA_SHORTLIST_MAX_ACTIVE = 8

DEFAULT_MAX_AUTONOMOUS_TURNS_PER_PLAN = 10
DEFAULT_MAX_AUTONOMOUS_TURNS_PER_SESSION = 20

CODING_MAX_ITERATIONS = 40
CODING_MAX_SELF_CORRECTIONS = 7

# Hard safety ceiling; never operator-tunable.
ADAPTIVE_BUDGET_HARD_CAP: int = 128

RESEARCH_CHECKPOINT_INTERVAL = 1
RESEARCH_MAX_RESUME_COUNT = 10
RESEARCH_MAX_ITERATIONS = 5

OBSERVE_POLL_INTERVAL_SECONDS = 30
OBSERVE_TIMEOUT_SECONDS = 600

RETRIEVAL_SHORTLIST_K = 6
RETRIEVAL_SHORTLIST_MAX = 24
DIRECT_PROMPT_BUDGET_TOKENS = 220
MAX_SKILLS_PER_SESSION = 4
TOOL_OUTCOME_SUCCESS_ALLOWLIST = frozenset(
    {
        "web.search",
        "web.fetch",
        "weather",
        "exec.run",
    }
)


def normalize_skill_selection_strategy(raw: Any) -> str:
    strategy = str(raw or "llm").strip().lower() or "llm"
    if strategy != "llm":
        _LOGGER.warning(
            "Invalid skill_selection_strategy=%r; falling back to 'llm'",
            strategy,
        )
        return "llm"
    return "llm"


class AdapterSubConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SessctlConfig(AdapterSubConfig):
    db_path: str | None = None


class ContextCtlConfig(AdapterSubConfig):
    mode: Literal["builder", "service"] = "builder"


class LlmctlConfig(AdapterSubConfig):
    config_path: str | None = None


class OsctlConfig(AdapterSubConfig):
    policy_path: str | None = None


class A2actlConfig(AdapterSubConfig):
    state_db_path: str | None = None
    audit_db_path: str | None = None


class AdaptersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["local", "core", "auto"] = "auto"
    sessctl: SessctlConfig = Field(default_factory=SessctlConfig)
    ctxctl: ContextCtlConfig = Field(default_factory=ContextCtlConfig)
    llmctl: LlmctlConfig = Field(default_factory=LlmctlConfig)
    osctl: OsctlConfig = Field(default_factory=OsctlConfig)
    a2actl: A2actlConfig = Field(default_factory=A2actlConfig)


try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    yaml = None  # type: ignore[assignment]


FailureStrategy = Literal["replan", "halt", "retry", "skip"]
FeasibilityRecheckTrigger = Literal["tool_dependent", "high_risk", "adaptive", "always"]
HardInfeasibilityAction = Literal["replan", "pause"]


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_retries_per_step: int = Field(default=2, ge=0)
    max_replans: int = Field(default=8, ge=0)
    plan_checkpoint_interval: int = Field(default=5, ge=0)
    plan_max_iterations: int = Field(default=64, ge=1)
    plan_consecutive_failure_limit: int = Field(default=3, ge=1)
    step_failure_strategy: FailureStrategy = "replan"
    adaptive_plan_revision_enabled: bool = True
    adaptive_replan_retained_step_outputs: int = Field(default=2, ge=0)
    continuous_feasibility_rechecks_enabled: bool = False
    continuous_feasibility_recheck_interval: int = Field(default=1, ge=1)
    continuous_feasibility_trigger: FeasibilityRecheckTrigger = "tool_dependent"
    continuous_feasibility_hard_action: HardInfeasibilityAction = "replan"


class PlanAutoScaleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_llm_calls: int = Field(default=128, ge=1)
    max_ticks: int = Field(default=128, ge=1)
    max_tokens: int = Field(default=500_000, ge=1000)
    max_elapsed_ms: int = Field(default=300_000, ge=1000)
    base_overhead_ms: int = Field(default=20_000, ge=0)
    per_step_time_ms: int = Field(default=15_000, ge=1)


class ReflectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    auto_save_lessons: bool = True
    stage_policy_candidates: bool = True
    governance_progress_checkpoints_enabled: bool = True
    governance_step_risk_gate_enabled: bool = True
    reserved_llm_calls: int = Field(default=1, ge=0)


class IdempotencyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    cache_size: int = Field(default=200, ge=1)


class ClarifyConfig(BaseModel):
    # extra="ignore" for backward compat with old configs that have guard fields
    model_config = ConfigDict(extra="ignore")

    default_mode: BrainMode = BrainMode.GUIDED
    default_policy: ClarifyPolicy = ClarifyPolicy.ASK_IF_AMBIGUOUS
    max_questions_per_turn: int = Field(default=5, ge=1, le=20)
    ask_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    allow_non_blocking: bool = True
    one_by_one_questions: bool = False
    timeout_seconds: int = Field(default=3600, ge=60)
    handle_unanswered_policy: Literal["error", "assume_default", "abort"] = (
        "assume_default"
    )


class MissionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_turns_per_mission: int = Field(default=4, ge=1, le=64)


@dataclass
class RunnerOptions:
    max_retries_per_step: int = 2
    max_replans: int = 8
    plan_checkpoint_interval: int = 5
    plan_max_iterations: int = 64
    plan_consecutive_failure_limit: int = 3
    adaptive_plan_revision_enabled: bool = True
    adaptive_replan_retained_step_outputs: int = 2
    continuous_feasibility_rechecks_enabled: bool = False
    continuous_feasibility_recheck_interval: int = 1
    continuous_feasibility_trigger: str = "tool_dependent"
    continuous_feasibility_hard_action: str = "replan"
    plan_auto_scale_max_llm_calls: int = 128
    plan_auto_scale_max_ticks: int = 128
    plan_auto_scale_max_tokens: int = 500_000
    plan_auto_scale_max_elapsed_ms: int = 300_000
    plan_auto_scale_base_overhead_ms: int = 20_000
    plan_auto_scale_per_step_time_ms: int = 15_000
    reflection_enabled: bool = True
    governance_progress_checkpoints_enabled: bool = True
    governance_step_risk_gate_enabled: bool = True
    reflection_reserved_llm_calls: int = 1
    idempotency_enabled: bool = True
    idempotency_cache_size: int = 200
    metactl_enabled: bool = True
    metactl_config: MetaRuntimeConfig = field(default_factory=MetaRuntimeConfig)
    failure_strategy: str = "replan"
    clarify_config: ClarifyConfig = field(default_factory=ClarifyConfig)
    mission_config: MissionConfig = field(default_factory=MissionConfig)
    complex_request_plan_policy: str = "balanced"
    memory_policy_snapshot: dict[str, Any] = field(default_factory=dict)
    skill_selection_strategy: str = "llm"
    tool_schema_shortlisting_enabled: bool = TOOL_SCHEMA_SHORTLISTING_ENABLED
    max_skills_per_session: int = MAX_SKILLS_PER_SESSION
    outcome_attribution_config: OutcomeAttributionConfig = field(
        default_factory=OutcomeAttributionConfig
    )
    success_memory_config: SuccessMemoryConfig = field(
        default_factory=SuccessMemoryConfig
    )
    auto_fact_extraction_config: AutoFactExtractionConfig = field(
        default_factory=AutoFactExtractionConfig
    )
    proactive_autonomous_entrypoint_config: ProactiveAutonomousEntrypointConfig = field(
        default_factory=ProactiveAutonomousEntrypointConfig
    )
    adaptive_budget_config: AdaptiveBudgetConfig = field(
        default_factory=AdaptiveBudgetConfig
    )
    budget_telemetry_config: BudgetTelemetryConfig = field(
        default_factory=BudgetTelemetryConfig
    )
    autonomous_continuation_enabled: bool = True
    autonomous_continuation_max_per_plan: int = DEFAULT_MAX_AUTONOMOUS_TURNS_PER_PLAN
    autonomous_continuation_max_per_session: int = (
        DEFAULT_MAX_AUTONOMOUS_TURNS_PER_SESSION
    )

    def __post_init__(self) -> None:
        self.plan_checkpoint_interval = max(
            0, int(getattr(self, "plan_checkpoint_interval", 5) or 0)
        )
        self.plan_max_iterations = max(
            1, int(getattr(self, "plan_max_iterations", 64) or 64)
        )
        self.plan_consecutive_failure_limit = max(
            1, int(getattr(self, "plan_consecutive_failure_limit", 3) or 3)
        )
        self.adaptive_plan_revision_enabled = bool(
            getattr(self, "adaptive_plan_revision_enabled", True)
        )
        self.adaptive_replan_retained_step_outputs = max(
            0,
            int(getattr(self, "adaptive_replan_retained_step_outputs", 2) or 0),
        )
        self.continuous_feasibility_rechecks_enabled = bool(
            getattr(self, "continuous_feasibility_rechecks_enabled", False)
        )
        self.continuous_feasibility_recheck_interval = max(
            1,
            int(getattr(self, "continuous_feasibility_recheck_interval", 1) or 1),
        )
        raw_feasibility_trigger = (
            str(
                getattr(self, "continuous_feasibility_trigger", "tool_dependent")
                or "tool_dependent"
            )
            .strip()
            .lower()
        )
        if raw_feasibility_trigger not in {
            "tool_dependent",
            "high_risk",
            "adaptive",
            "always",
        }:
            raw_feasibility_trigger = "tool_dependent"
        self.continuous_feasibility_trigger = raw_feasibility_trigger
        raw_hard_action = (
            str(
                getattr(self, "continuous_feasibility_hard_action", "replan")
                or "replan"
            )
            .strip()
            .lower()
        )
        if raw_hard_action not in {"replan", "pause"}:
            raw_hard_action = "replan"
        self.continuous_feasibility_hard_action = raw_hard_action
        self.plan_auto_scale_max_llm_calls = max(
            1, int(getattr(self, "plan_auto_scale_max_llm_calls", 128) or 128)
        )
        self.plan_auto_scale_max_ticks = max(
            1, int(getattr(self, "plan_auto_scale_max_ticks", 128) or 128)
        )
        self.plan_auto_scale_max_tokens = max(
            1000,
            int(getattr(self, "plan_auto_scale_max_tokens", 500_000) or 500_000),
        )
        raw_max_elapsed_ms = getattr(self, "plan_auto_scale_max_elapsed_ms", 300_000)
        raw_base_overhead_ms = getattr(self, "plan_auto_scale_base_overhead_ms", 20_000)
        raw_per_step_time_ms = getattr(self, "plan_auto_scale_per_step_time_ms", 15_000)
        self.plan_auto_scale_max_elapsed_ms = max(
            1000,
            int(300_000 if raw_max_elapsed_ms is None else raw_max_elapsed_ms),
        )
        self.plan_auto_scale_base_overhead_ms = max(
            0,
            int(20_000 if raw_base_overhead_ms is None else raw_base_overhead_ms),
        )
        self.plan_auto_scale_per_step_time_ms = max(
            1,
            int(15_000 if raw_per_step_time_ms is None else raw_per_step_time_ms),
        )
        self.governance_progress_checkpoints_enabled = bool(
            getattr(self, "governance_progress_checkpoints_enabled", True)
        )
        self.governance_step_risk_gate_enabled = bool(
            getattr(self, "governance_step_risk_gate_enabled", True)
        )
        self.reflection_reserved_llm_calls = max(
            0, int(getattr(self, "reflection_reserved_llm_calls", 1) or 0)
        )
        self.metactl_config = _coerce_meta_runtime_config(self.metactl_config)
        self.skill_selection_strategy = normalize_skill_selection_strategy(
            getattr(self, "skill_selection_strategy", "llm")
        )
        self.max_skills_per_session = max(
            1,
            int(
                getattr(self, "max_skills_per_session", MAX_SKILLS_PER_SESSION)
                or MAX_SKILLS_PER_SESSION
            ),
        )
        raw_mission_config = getattr(self, "mission_config", MissionConfig())
        if isinstance(raw_mission_config, MissionConfig):
            self.mission_config = raw_mission_config
        else:
            self.mission_config = MissionConfig.model_validate(raw_mission_config)
        raw_outcome_attribution = getattr(
            self,
            "outcome_attribution_config",
            OutcomeAttributionConfig(),
        )
        if isinstance(raw_outcome_attribution, OutcomeAttributionConfig):
            self.outcome_attribution_config = raw_outcome_attribution
        else:
            self.outcome_attribution_config = OutcomeAttributionConfig.model_validate(
                raw_outcome_attribution
            )
        raw_success_memory = getattr(
            self,
            "success_memory_config",
            SuccessMemoryConfig(),
        )
        if isinstance(raw_success_memory, SuccessMemoryConfig):
            self.success_memory_config = raw_success_memory
        else:
            self.success_memory_config = SuccessMemoryConfig.model_validate(
                raw_success_memory
            )
        raw_afe = getattr(
            self,
            "auto_fact_extraction_config",
            AutoFactExtractionConfig(),
        )
        if isinstance(raw_afe, AutoFactExtractionConfig):
            self.auto_fact_extraction_config = raw_afe
        else:
            self.auto_fact_extraction_config = AutoFactExtractionConfig.model_validate(
                raw_afe
            )
        raw_aib = getattr(
            self,
            "adaptive_budget_config",
            AdaptiveBudgetConfig(),
        )
        if isinstance(raw_aib, AdaptiveBudgetConfig):
            self.adaptive_budget_config = raw_aib
        else:
            self.adaptive_budget_config = AdaptiveBudgetConfig.model_validate(raw_aib)
        raw_budget_telemetry = getattr(
            self,
            "budget_telemetry_config",
            BudgetTelemetryConfig(),
        )
        if isinstance(raw_budget_telemetry, BudgetTelemetryConfig):
            self.budget_telemetry_config = raw_budget_telemetry
        else:
            self.budget_telemetry_config = BudgetTelemetryConfig.model_validate(
                raw_budget_telemetry
            )
        raw_pae = getattr(
            self,
            "proactive_autonomous_entrypoint_config",
            ProactiveAutonomousEntrypointConfig(),
        )
        if isinstance(raw_pae, ProactiveAutonomousEntrypointConfig):
            self.proactive_autonomous_entrypoint_config = raw_pae
        else:
            self.proactive_autonomous_entrypoint_config = (
                ProactiveAutonomousEntrypointConfig.model_validate(raw_pae)
            )
        self.autonomous_continuation_enabled = bool(
            getattr(self, "autonomous_continuation_enabled", True)
        )
        raw_per_plan = getattr(
            self,
            "autonomous_continuation_max_per_plan",
            DEFAULT_MAX_AUTONOMOUS_TURNS_PER_PLAN,
        )
        if raw_per_plan is None:
            raw_per_plan = DEFAULT_MAX_AUTONOMOUS_TURNS_PER_PLAN
        self.autonomous_continuation_max_per_plan = max(1, int(raw_per_plan))
        raw_per_session = getattr(
            self,
            "autonomous_continuation_max_per_session",
            DEFAULT_MAX_AUTONOMOUS_TURNS_PER_SESSION,
        )
        if raw_per_session is None:
            raw_per_session = DEFAULT_MAX_AUTONOMOUS_TURNS_PER_SESSION
        self.autonomous_continuation_max_per_session = max(1, int(raw_per_session))


class MetaCtlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    high_risk_score_threshold: int = 70
    low_grounding_threshold: float = 0.5
    repeat_error_threshold: int = 2
    stall_ticks_threshold: int = 6
    no_new_facts_threshold: int = 4
    low_progress_iterations_without_new_typed_record_threshold: int = 3
    low_progress_repeated_arg_signature_threshold: int = 2
    low_progress_unique_tool_call_count_delta_threshold: int = 2
    budget_pressure_threshold: float = 0.8
    ruleset_version: str = "metactl.v1"
    high_risk_verification_mode: VerificationMode = VerificationMode.panel_judge
    medium_risk_verification_mode: VerificationMode = VerificationMode.rule_based

    def to_meta_config(self) -> MetaRuntimeConfig:
        return MetaRuntimeConfig(
            high_risk_score_threshold=self.high_risk_score_threshold,
            low_grounding_threshold=self.low_grounding_threshold,
            repeat_failure_threshold=self.repeat_error_threshold,
            loop_count_threshold=self.stall_ticks_threshold,
            replan_count_threshold=self.no_new_facts_threshold,
            low_progress_iterations_without_new_typed_record_threshold=(
                self.low_progress_iterations_without_new_typed_record_threshold
            ),
            low_progress_repeated_arg_signature_threshold=(
                self.low_progress_repeated_arg_signature_threshold
            ),
            low_progress_unique_tool_call_count_delta_threshold=(
                self.low_progress_unique_tool_call_count_delta_threshold
            ),
            budget_pressure_threshold=self.budget_pressure_threshold,
            ruleset_version=self.ruleset_version,
            high_risk_verification_mode=self.high_risk_verification_mode,
            medium_risk_verification_mode=self.medium_risk_verification_mode,
        )


def _coerce_meta_runtime_config(raw: Any) -> MetaRuntimeConfig:
    if isinstance(raw, MetaRuntimeConfig):
        return raw
    if raw is None:
        return MetaRuntimeConfig()
    return MetaRuntimeConfig(
        ruleset_version=str(getattr(raw, "ruleset_version", "metactl.v1")),
        high_risk_score_threshold=int(getattr(raw, "high_risk_score_threshold", 70)),
        medium_risk_score_threshold=int(
            getattr(raw, "medium_risk_score_threshold", 40)
        ),
        low_grounding_threshold=float(getattr(raw, "low_grounding_threshold", 0.5)),
        low_intent_confidence_threshold=float(
            getattr(raw, "low_intent_confidence_threshold", 0.6)
        ),
        high_ambiguity_threshold=float(getattr(raw, "high_ambiguity_threshold", 0.7)),
        repeat_failure_threshold=int(
            getattr(
                raw,
                "repeat_failure_threshold",
                getattr(raw, "repeat_error_threshold", 2),
            )
        ),
        loop_count_threshold=int(
            getattr(
                raw, "loop_count_threshold", getattr(raw, "stall_ticks_threshold", 6)
            )
        ),
        replan_count_threshold=int(
            getattr(
                raw, "replan_count_threshold", getattr(raw, "no_new_facts_threshold", 4)
            )
        ),
        low_progress_iterations_without_new_typed_record_threshold=int(
            getattr(
                raw,
                "low_progress_iterations_without_new_typed_record_threshold",
                3,
            )
        ),
        low_progress_repeated_arg_signature_threshold=int(
            getattr(raw, "low_progress_repeated_arg_signature_threshold", 2)
        ),
        low_progress_unique_tool_call_count_delta_threshold=int(
            getattr(raw, "low_progress_unique_tool_call_count_delta_threshold", 2)
        ),
        budget_pressure_threshold=float(getattr(raw, "budget_pressure_threshold", 0.8)),
        tool_degraded_threshold=float(getattr(raw, "tool_degraded_threshold", 0.8)),
        high_risk_verification_mode=getattr(
            raw, "high_risk_verification_mode", VerificationMode.panel_judge
        ),
        medium_risk_verification_mode=getattr(
            raw, "medium_risk_verification_mode", VerificationMode.rule_based
        ),
    )


class BrainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    agent_id: str = "router-agent"
    role: str = "general"
    thinking: str = "minimal"
    llm_profiles: LLMProfiles = Field(
        default_factory=lambda: LLMProfiles(
            decide_model="decide-default",
            plan_model="plan-default",
            act_model=None,
            reflect_model="reflect-default",
            summarize_model="summarize-default",
        )
    )
    model_capability_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tool_policy: str | dict[str, Any] | None = None
    memory_read_scopes: list[str] = Field(default_factory=list)
    memory_write_scopes: dict[str, Any] = Field(default_factory=dict)
    default_act_profile: str | None = None
    skill: str | list[str] | None = None
    skill_catalog: list[str] = Field(default_factory=list)
    budgets: AgentBudgets
    retries: RetryConfig = Field(default_factory=RetryConfig)
    plan_auto_scale: PlanAutoScaleConfig = Field(default_factory=PlanAutoScaleConfig)
    reflection: ReflectionConfig = Field(default_factory=ReflectionConfig)
    idempotency: IdempotencyConfig = Field(default_factory=IdempotencyConfig)
    clarify: ClarifyConfig = Field(
        default_factory=ClarifyConfig
    )
    mission: MissionConfig = Field(default_factory=MissionConfig)
    skill_selection_strategy: str = "llm"
    max_skills_per_session: int = Field(default=MAX_SKILLS_PER_SESSION, ge=1)
    outcome_attribution: OutcomeAttributionConfig = Field(
        default_factory=OutcomeAttributionConfig
    )
    success_memory: SuccessMemoryConfig = Field(default_factory=SuccessMemoryConfig)
    auto_fact_extraction: AutoFactExtractionConfig = Field(
        default_factory=AutoFactExtractionConfig
    )
    proactive_autonomous_entrypoint: ProactiveAutonomousEntrypointConfig = Field(
        default_factory=ProactiveAutonomousEntrypointConfig
    )
    adaptive_budget: AdaptiveBudgetConfig = Field(default_factory=AdaptiveBudgetConfig)
    budget_telemetry: BudgetTelemetryConfig = Field(
        default_factory=BudgetTelemetryConfig
    )
    metactl: MetaCtlConfig = Field(default_factory=MetaCtlConfig)
    adapters: AdaptersConfig = Field(default_factory=AdaptersConfig)

    def to_agent_profile(self) -> AgentProfile:
        defaults = AgentDefaults(
            auto_save_lessons=self.reflection.auto_save_lessons,
            auto_stage_policy_candidates=self.reflection.stage_policy_candidates,
        )
        return AgentProfile(
            agent_id=self.agent_id,
            role=self.role,
            thinking=self.thinking,
            llm_profiles=self.llm_profiles,
            model_capability_overrides=self.model_capability_overrides,
            tool_policy=self.tool_policy,
            memory_read_scopes=self.memory_read_scopes,
            memory_write_scopes=self.memory_write_scopes,
            default_act_profile=self.default_act_profile,
            skill=self.skill,
            skill_catalog=list(self.skill_catalog),
            max_skills_per_session=self.max_skills_per_session,
            budgets=self.budgets,
            defaults=defaults,
            outcome_attribution=self.outcome_attribution.model_copy(deep=True),
            success_memory=self.success_memory.model_copy(deep=True),
            auto_fact_extraction=self.auto_fact_extraction.model_copy(deep=True),
            proactive_autonomous_entrypoint=self.proactive_autonomous_entrypoint.model_copy(
                deep=True
            ),
            adaptive_budget=self.adaptive_budget.model_copy(deep=True),
            budget_telemetry=self.budget_telemetry.model_copy(deep=True),
        )


StateMachineConfig = BrainConfig


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    brain: BrainConfig = Field(
        validation_alias=AliasChoices("brain", "brainctl"),
        serialization_alias="brain",
    )

    @property
    def brainctl(self) -> BrainConfig:
        return self.brain


def from_base_config(
    *,
    base_config: OpenMinionConfig,
    home_root: Path,
    data_root: Path,
) -> RuntimeConfig:
    candidates = tuple(
        (home_root / name).resolve(strict=False) for name in DEFAULT_CONFIG_FILENAMES
    ) + tuple(
        (data_root / DEFAULT_INTEGRATED_CONFIG_SUBDIR / name).resolve(strict=False)
        for name in DEFAULT_CONFIG_FILENAMES
    )
    for candidate in candidates:
        if candidate.exists():
            return load_config(candidate)

    llm_profiles = _default_llm_profiles(base_config)
    budgets = _default_budgets(base_config)
    default_agent_id = resolve_default_agent_id(base_config)
    default_profile = base_config.agents[default_agent_id]
    agent_id = str(default_profile.name or "").strip() or default_agent_id

    brain_config = BrainConfig(
        agent_id=agent_id,
        role="general",
        thinking=str(default_profile.thinking or "minimal"),
        llm_profiles=llm_profiles,
        tool_policy=None,
        memory_read_scopes=[],
        memory_write_scopes={},
        default_act_profile=str(default_profile.default_act_profile or "").strip()
        or None,
        skill=default_profile.skill,
        skill_catalog=list(default_profile.skill_catalog or []),
        budgets=budgets,
    )
    brain_overrides = (
        dict(getattr(base_config, "module_configs", {}) or {}).get("brain") or {}
    )
    if brain_overrides:
        payload = brain_config.model_dump(mode="python")
        payload.update(brain_overrides)
        brain_config = BrainConfig.model_validate(payload)

    return RuntimeConfig(brain=brain_config)


def load_config(path: Path) -> RuntimeConfig:
    if yaml is None:
        raise RuntimeError("pyyaml is required to load brain config")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "brain" not in raw and "brainctl" not in raw:
        raise ValueError(
            "Missing 'brain' root key in config (legacy 'brainctl' also accepted)"
        )
    return RuntimeConfig.model_validate(raw)


def _default_llm_profiles(base_config: OpenMinionConfig) -> LLMProfiles:
    try:
        default_agent_id = resolve_default_agent_id(base_config)
        provider_name = (
            str(base_config.agents[default_agent_id].provider or "").strip().lower()
        )
    except Exception:  # noqa: BLE001
        provider_name = ""
    provider_cfg = (
        getattr(base_config.providers, provider_name, None) if provider_name else None
    )
    model = getattr(provider_cfg, "model", None) if provider_cfg is not None else None
    if not model:
        return LLMProfiles(
            decide_model="decide-default",
            plan_model="plan-default",
            act_model=None,
            reflect_model="reflect-default",
            summarize_model="summarize-default",
        )
    return LLMProfiles(
        decide_model=str(model),
        plan_model=str(model),
        act_model=None,
        reflect_model=str(model),
        summarize_model=str(model),
    )


def _default_budgets(base_config: OpenMinionConfig) -> AgentBudgets:
    max_ticks = max(1, int(base_config.runtime.agent_loop_max_steps))
    max_tool_calls = max(0, int(base_config.security.tool_policy.max_calls_per_run))
    max_total_tokens = int(base_config.runtime.session_context_token_budget)
    if max_total_tokens <= 0:
        max_total_tokens = 2000
    max_elapsed_ms = max(
        1000, int(base_config.runtime.brain_turn_timeout_seconds) * 1000
    )
    return AgentBudgets(
        max_ticks_per_user_turn=max_ticks,
        max_tool_calls=max_tool_calls,
        # Allow explicit delegated execution by default without turning A2A into
        # an unbounded fallback path.
        max_a2a_calls=max(1, min(max_ticks, 2)),
        max_total_llm_tokens=max_total_tokens,
        max_elapsed_ms=max_elapsed_ms,
    )
