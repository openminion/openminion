from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FreshnessDomain(str, Enum):
    GENERAL = "general"
    FINANCE = "finance"
    NEWS = "news"
    WEATHER = "weather"
    REGULATION = "regulation"
    SHOPPING = "shopping"
    SPORTS = "sports"
    OTHER = "other"


class FreshnessAnswerMode(str, Enum):
    LOCAL_ONLY = "local_only"
    BROWSE_THEN_ANSWER = "browse_then_answer"


class FreshnessContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = ""
    domain: FreshnessDomain = FreshnessDomain.GENERAL
    time_sensitive: bool = False
    needs_live_data: bool = False
    needs_sources: bool = False
    needs_exact_date: bool = False
    answer_mode: FreshnessAnswerMode = FreshnessAnswerMode.LOCAL_ONLY
    reason: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _normalize_requirements(self) -> "FreshnessContract":
        if self.needs_live_data:
            self.time_sensitive = True
            self.answer_mode = FreshnessAnswerMode.BROWSE_THEN_ANSWER
        if self.needs_sources or self.needs_exact_date:
            self.time_sensitive = True
        if not self.time_sensitive:
            self.needs_live_data = False
            self.needs_sources = False
            self.needs_exact_date = False
            self.answer_mode = FreshnessAnswerMode.LOCAL_ONLY
        return self


class FreshnessObligations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_live_data: bool = False
    require_sources: bool = False
    require_exact_date: bool = False
    require_explicit_failure_wording: bool = False
    answer_mode: FreshnessAnswerMode = FreshnessAnswerMode.LOCAL_ONLY


class FreshnessDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classifier_mode: str = ""
    classifier_model: str = ""
    classified_at: str = ""
    notes: list[str] = Field(default_factory=list)
    verifier_notes: list[str] = Field(default_factory=list)


__all__ = [
    "FreshnessAnswerMode",
    "FreshnessContract",
    "FreshnessDiagnostics",
    "FreshnessDomain",
    "FreshnessObligations",
]
