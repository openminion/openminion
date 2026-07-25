# ruff: noqa: F403,F405
from .common import *
from .budget import MissionBudgetEnvelope

class MissionJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: MissionJudgmentOutcome = "continue"
    reason: str = ""
    final_answer: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

class PostActionJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: PostActionJudgmentOutcome
    reason: str = ""
    user_message: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

class MissionState(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    mission_id: str = Field(..., min_length=1)
    objective: str = Field(..., min_length=1)
    status: MissionLifecycleStatus = MissionStatus.ACTIVE
    started_at: str = Field(default_factory=iso_now)
    last_progress_at: str | None = None
    completed_at: str | None = None
    task_id: str | None = None
    budget: MissionBudgetEnvelope
    completion_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    latest_judgment: MissionJudgment | None = None
    latest_reason: str = ""
    latest_reset_policy: str = ""
    latest_route_action: str = ""
