# ruff: noqa: F403,F405
from .common import *
from .profiles import ProviderProfile


class RubricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    weight: float = 1.0


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str = ""
    criteria: list[RubricCriterion] = Field(default_factory=list)


class NormalizationRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strip_whitespace: bool = True
    lowercase: bool = False


class DisagreementConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    method: Literal["simple_text", "json_field_diff"] = "simple_text"
    threshold: float = 0.75
    max_excerpt_chars: int = 240


class EnsembleTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    mode: EnsembleMode
    providers: list[str] = Field(default_factory=list)
    judge_profile_id: Optional[str] = None
    selection_policy: SelectionPolicyName = "pick_primary_if_ok"
    rubric: Optional[Rubric] = None
    timeout_ms: int = 30000
    max_parallel: int = 2
    stop_early: bool = False
    normalization: Optional[NormalizationRules] = None
    disagreement: Optional[DisagreementConfig] = None


class LLMCatalogDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_timeout_ms: int = 30000
    default_max_parallel: int = 2
    default_selection_policy: SelectionPolicyName = "pick_primary_if_ok"
    default_rubric: Optional[Rubric] = None


class GlobalLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_parallel_global: int = 8
    max_inflight_requests: Optional[int] = None
    max_tokens_per_call_hard: int = 8192


class CatalogLoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_raw_provider_payloads: bool = False
    store_normalized_candidates: bool = False
    store_ensemble_report: bool = True
    emit_events: bool = True


class SecretResolutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    env_prefix: str = ""


class LLMCatalogConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    profiles: list[ProviderProfile] = Field(default_factory=list)
    ensembles: list[EnsembleTemplate] = Field(default_factory=list)
    defaults: LLMCatalogDefaults = Field(default_factory=LLMCatalogDefaults)
    limits: GlobalLimits = Field(default_factory=GlobalLimits)
    logging: CatalogLoggingConfig = Field(default_factory=CatalogLoggingConfig)
    secrets: SecretResolutionConfig = Field(default_factory=SecretResolutionConfig)

    @model_validator(mode="after")
    def _validate_ids(self) -> "LLMCatalogConfig":
        profile_ids = [item.id for item in self.profiles]
        ensemble_ids = [item.id for item in self.ensembles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError(
                "profiles.*.id must be unique"
            )  # allow-bare-raise: pydantic @model_validator body
        if len(ensemble_ids) != len(set(ensemble_ids)):
            raise ValueError(
                "ensembles.*.id must be unique"
            )  # allow-bare-raise: pydantic @model_validator body
        return self
