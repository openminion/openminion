# ruff: noqa: F403,F405
from .common import *

class LastResultSummary(BaseModel):
    """ASPM-03: Structured summary of last_result for prompt projection."""

    command: Optional[str] = None
    tool: Optional[str] = None
    status: str = "unknown"
    exit_code: Optional[int] = None
    summary: str = ""
    artifact_refs: List[str] = Field(default_factory=list)


class IntentExecutionPromptView(BaseModel):
    intent_id: str
    status: str
    depends_on: List[str] = Field(default_factory=list)
    last_step_index: Optional[int] = None
    updated_at: Optional[str] = None


class PlanProgressPromptView(BaseModel):
    has_plan: bool = False
    step_count: int = 0
    cursor: int = 0


class ActiveStatePromptView(BaseModel):
    """Compact prompt-facing projection of active state."""

    state_ref: Optional[str] = None
    task_id: Optional[str] = None
    task_description: Optional[str] = None
    status: str = "idle"
    last_result: Optional[LastResultSummary] = None
    open_questions: List[str] = Field(default_factory=list)
    declared_sub_intents: List[str] = Field(default_factory=list)
    intent_execution_states: List[IntentExecutionPromptView] = Field(
        default_factory=list
    )
    plan_progress: Optional[PlanProgressPromptView] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
