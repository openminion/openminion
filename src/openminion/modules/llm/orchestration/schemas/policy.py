# ruff: noqa: F403,F405
from .common import *
from .catalog import EnsembleTemplate, Rubric


class SingleRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["single"]
    profile_id: str = Field(..., min_length=1)
    params_override: Optional[dict[str, Any]] = None
    timeout_ms: Optional[int] = None


class EnsembleRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["ensemble"]
    strategy_id: Optional[str] = None
    strategy_inline: Optional[EnsembleTemplate] = None
    providers: Optional[list[str]] = None
    judge_profile_id: Optional[str] = None
    selection_policy: Optional[SelectionPolicyName] = None
    rubric: Optional[Rubric] = None
    timeout_ms: Optional[int] = None
    max_parallel: Optional[int] = None
    stop_early: Optional[bool] = None
    fanout: Optional[int] = None

    @model_validator(mode="after")
    def _validate_ensemble_route(self) -> "EnsembleRoute":
        if self.strategy_id or self.strategy_inline or self.providers:
            return self
        raise ValueError(  # allow-bare-raise: pydantic @model_validator body
            "Ensemble route requires strategy_id, strategy_inline, or providers"
        )


LLMRoute = SingleRoute | EnsembleRoute


class AgentLLMBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens_per_call: int = 2048
    max_tokens_per_turn: int = 8192
    max_cost_per_turn: Optional[float] = None
    max_parallel: int = 2
    max_ensemble_fanout: int = 3
    max_time_ms_per_turn: int = 120000


class FallbackPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fallback_profile_ids: list[str] = Field(default_factory=list)
    fallback_mode: FallbackMode = "single"
    on_error_codes: Optional[list[str]] = None
    max_fallback_attempts: int = 1


class AgentLLMPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_route: Optional[LLMRoute] = None
    by_purpose: dict[str, LLMRoute] = Field(default_factory=dict)
    allow_profiles: Optional[list[str]] = None
    deny_profiles: Optional[list[str]] = None
    budgets: AgentLLMBudgets = Field(default_factory=AgentLLMBudgets)
    fallbacks: dict[str, FallbackPolicy] = Field(default_factory=dict)
    overrides: dict[str, Any] = Field(default_factory=dict)
