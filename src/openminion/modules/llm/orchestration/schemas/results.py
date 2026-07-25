# ruff: noqa: F403,F405
from .common import *


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: Optional[float] = None


class CandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    candidate_id: str
    profile_id: str
    provider: str
    model: str
    status: CandidateStatus
    text: Optional[str] = None
    json_output: Optional[dict[str, Any]] = Field(
        default=None, alias="json", serialization_alias="json"
    )
    usage: Usage = Field(default_factory=Usage)
    error: Optional[ResponseError] = None
    raw_artifact_ref: Optional[str] = None


class SelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    winner_candidate_id: str
    winner_profile_id: str
    scores: Optional[dict[str, float]] = None
    reasons: list[str] = Field(default_factory=list)
    risk_flags: Optional[list[str]] = None


class DisagreementCluster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_ids: list[str] = Field(default_factory=list)
    excerpt: str = ""


class DisagreementReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    clusters: list[DisagreementCluster] = Field(default_factory=list)
    json_diffs: Optional[dict[str, Any]] = None
    risk_flags: list[str] = Field(default_factory=list)


class UsageTotal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms_total: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: Optional[float] = None


class EnsembleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    mode: EnsembleMode
    candidates: list[CandidateResponse] = Field(default_factory=list)
    selection: Optional[SelectionResult] = None
    disagreement: Optional[DisagreementReport] = None
    usage_total: UsageTotal = Field(default_factory=UsageTotal)
