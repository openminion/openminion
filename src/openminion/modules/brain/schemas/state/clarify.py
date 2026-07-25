# ruff: noqa: F403,F405
from .common import *


class BrainMode(str, Enum):
    COMMAND = "command"
    GUIDED = "guided"
    AUTONOMOUS = "autonomous"
    BATCH = "batch"


class ClarifyPolicy(str, Enum):
    ALWAYS_ASK = "always_ask"
    ASK_IF_AMBIGUOUS = "ask_if_ambiguous"
    ASK_IF_RISKY = "ask_if_risky"
    ASSUME_DEFAULTS = "assume_defaults"
    SMART_ASSUME = "smart_assume"


class BudgetStopReason(str, Enum):
    TICKS_EXHAUSTED = "ticks_exhausted"
    TIME_EXHAUSTED = "time_exhausted"


class ClarifyQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=new_uuid, min_length=1)
    type: ClarifyQuestionType
    question: str = Field(..., min_length=1)
    description: str = ""
    options: list[str] | None = None
    default_value: str | None = None
    is_blocking: bool = True
    reason_code: str = ""
    source: str = ""
    requires_validation: bool = False
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)


class ClarifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    trace_id: str = Field(..., min_length=1)
    questions: list[ClarifyQuestion] = Field(default_factory=list)
    mode: BrainMode
    policy: ClarifyPolicy
    reason: str = ""
    context_snapshot: dict[str, Any] | None = None
    deadline: str | None = None


class ClarifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(..., min_length=1)
    trace_id: str = Field(..., min_length=1)
    answers: dict[str, str] = Field(default_factory=dict)
    unanswered_ids: list[str] = Field(default_factory=list)
