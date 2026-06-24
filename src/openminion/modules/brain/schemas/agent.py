from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .base import RiskLevel


class LLMProfiles(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decide_model: str = Field(..., min_length=1)
    plan_model: str = Field(..., min_length=1)
    act_model: str | None = None
    reflect_model: str = Field(..., min_length=1)
    summarize_model: str = Field(..., min_length=1)


class AgentBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_ticks_per_user_turn: int = Field(..., ge=1)
    max_tool_calls: int = Field(..., ge=0)
    max_a2a_calls: int = Field(..., ge=0)
    max_total_llm_tokens: int = Field(..., ge=0)
    max_elapsed_ms: int = Field(..., ge=1)


class AgentDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_tolerance: RiskLevel = "med"
    auto_save_lessons: bool = True
    auto_stage_policy_candidates: bool = True


class OutcomeAttributionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    success_feedback_delta: float = 0.05
    failure_feedback_delta: float = -0.10
    timeout_feedback_delta: float = -0.05
    max_memory_refs_per_command: int = Field(default=12, ge=1)
    include_fact_refs: bool = True
    include_procedure_refs: bool = True


class SuccessMemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_items_per_turn: int = Field(default=3, ge=1, le=10)
    procedure_enabled: bool = True
    tool_habit_enabled: bool = True
    require_closure_satisfied: bool = True
    require_all_steps_successful: bool = False
    min_item_confidence: float = Field(default=0.7, ge=0.0, le=1.0)


class ProactiveAutonomousEntrypointConfig(BaseModel):
    """PAE — Proactive Autonomous Entrypoint runtime configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    interval_seconds: int = Field(default=0, ge=0)
    user_activity_grace_seconds: int = Field(default=300, ge=0)
    max_consecutive_noops: int = Field(default=3, ge=0)


class AdaptiveBudgetConfig(BaseModel):
    """AIB — Adaptive Iteration Budget runtime configuration."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["interactive", "autonomous"] = "interactive"
    soft_cap: int = Field(default=24, ge=1, le=128)
    extend_by: int = Field(default=12, ge=1, le=64)
    max_extensions_per_turn: int = Field(default=3, ge=0, le=10)
    max_extensions_per_session: int = Field(default=10, ge=0, le=50)
    max_adaptive_noops_per_turn: int = Field(default=3, ge=0, le=20)
    idle_timeout_s: int = Field(default=300, ge=30, le=3600)


BudgetTelemetryGranularity = Literal["coarse", "fine"]


class BudgetTelemetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    granularity: BudgetTelemetryGranularity = "coarse"


class AutoFactExtractionConfig(BaseModel):
    """AFE — Auto-Fact Extraction runtime configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    model_tier: str = Field(default="reflect")
    max_items_per_turn: int = Field(default=5, ge=1, le=20)
    min_user_message_chars: int = Field(default=8, ge=1)
    initial_confidence: float = Field(default=0.3, ge=0.0, le=1.0)
    timeout_seconds: int = Field(default=20, ge=1, le=120)


class ModeProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    delegate_async: bool | None = None
    parallel_enabled: bool | None = None
    parallel_writes_enabled: bool | None = None
    max_parallel_workers: int | None = Field(default=None, ge=1, le=10)
    checkpoint_interval: int | None = Field(default=None, ge=1)
    max_resume_count: int | None = Field(default=None, ge=0)
    max_depth: int | None = Field(default=None, ge=0)
    priority_hint: int | None = None
    max_commands_per_turn: int | None = Field(default=None, ge=1)
    max_adaptive_iterations: int | None = Field(default=None, ge=1, le=100)
    max_adaptive_tool_calls_per_loop: int | None = Field(default=None, ge=1, le=100)
    max_adaptive_llm_calls_per_loop: int | None = Field(default=None, ge=1, le=100)
    adaptive_include_reflect: bool | None = None
    max_self_corrections: int | None = Field(default=None, ge=1, le=20)
    max_subtasks: int | None = Field(default=None, ge=2, le=20)
    max_decompose_depth: int | None = Field(default=None, ge=1, le=5)
    max_research_iterations: int | None = Field(default=None, ge=1, le=20)
    tool_schema_shortlisting_enabled: bool | None = None


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., min_length=1)
    role: str = Field(default="general", min_length=1)
    thinking: str = Field(default="minimal")
    llm_profiles: LLMProfiles
    default_act_profile: str | None = None
    skill: str | list[str] | None = None
    skill_catalog: list[str] = Field(default_factory=list)
    max_skills_per_session: int = Field(default=4, ge=1)
    tool_policy: str | dict[str, Any] | None = None
    memory_read_scopes: list[str] = Field(default_factory=list)
    memory_write_scopes: dict[str, Any] = Field(default_factory=dict)
    model_capability_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mode_config: dict[str, ModeProfileConfig] = Field(default_factory=dict)
    budgets: AgentBudgets
    defaults: AgentDefaults = Field(default_factory=AgentDefaults)
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
    # per-agent adaptive iteration budget. Controls soft cap,
    adaptive_budget: AdaptiveBudgetConfig = Field(default_factory=AdaptiveBudgetConfig)
    budget_telemetry: BudgetTelemetryConfig = Field(
        default_factory=BudgetTelemetryConfig
    )
    # operator policy for executing model-declared goals.
    goal_execution_policy: Literal["suggest", "auto_safe", "auto_full"] = Field(
        default="suggest",
        description=(
            "Operator policy controlling whether model-declared goals "
            "auto-execute or require user confirmation. Default 'suggest' "
            "(safe for all deployments)."
        ),
    )

    @field_validator("default_act_profile", mode="before")
    @classmethod
    def validate_default_act_profile(cls, value: Any) -> str | None:
        from ..act_profiles import normalize_default_act_profile

        normalized = normalize_default_act_profile(value)
        text = str(value or "").strip().lower()
        if value is None or not text or text == "auto":
            return normalized
        if normalized is None:
            raise ValueError(
                "default_act_profile must be None, '', 'auto', or a valid act profile"
            )
        return normalized
