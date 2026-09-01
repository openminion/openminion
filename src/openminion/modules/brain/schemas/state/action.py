# ruff: noqa: F403,F405
from .common import *


class ActionError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)


class ActionMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latency_ms: int | None = Field(default=None, ge=0)
    tokens_used: int | None = Field(default=None, ge=0)
    cost_estimate: float | None = Field(default=None, ge=0)


class MemoryUseRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record_id: str = Field(..., min_length=1)
    use_kind: Literal["used", "cited"]
    producer_kind: Literal["model", "tool", "action"]
    producer_id: str = Field(..., min_length=1)


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str = Field(..., min_length=1)
    status: ActionStatus
    summary: str = ""
    outputs: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    memory_refs: list[str] = Field(default_factory=list)
    memory_use_refs: list[MemoryUseRef] = Field(default_factory=list)
    error: ActionError | None = None
    metrics: ActionMetrics | None = None


class JobHandle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(..., min_length=1)
    command_id: str = Field(..., min_length=1)
    provider: Literal["tool", "a2actl"]
    status: Literal["pending", "running", "done", "failed"]
    poll_after_ms: int = Field(default=1000, ge=1)
    created_at: str = Field(default_factory=iso_now)


class ReflectReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    agent_id: str = Field(..., min_length=1)
    command_id: str = Field(..., min_length=1)
    outcome: ReflectOutcome
    failure_type: FailureType | None = None
    root_cause: str = ""
    evidence_refs: list[ArtifactRef] = Field(default_factory=list)
    fixes: list[FixItem] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal[
        "ALLOW",
        "DENY",
        "REQUIRE_CONFIRMATION",
        "MODIFY",
        "REQUIRE_CLARIFICATION",
    ]
    explanation: str = ""
    patched_command: Command | None = None
    require_clarification: bool = False
    clarification_question: str | None = None
    approval_id: str | None = None
