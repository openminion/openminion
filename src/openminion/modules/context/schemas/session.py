# ruff: noqa: F403,F405
from .common import *

class SessionTurn(BaseModel):
    turn_id: str
    role: str
    content: str
    ts: Optional[str] = None
    is_error: bool = False


class SessionToolEvent(BaseModel):
    event_id: str
    tool_name: str
    excerpt: str
    artifact_refs: List[str] = Field(default_factory=list)


class SessionSlice(BaseModel):
    session_id: str
    slice_version: str
    last_event_id: Optional[str] = None
    summary_short: str
    summary_long: Optional[str] = None
    conversation_summary: str = ""
    active_task_plan: Optional[TaskPlan] = None
    continuation: Optional[Dict[str, Any]] = None
    task_digest: Optional[Dict[str, Any]] = None
    pending_trailer_feedback: Optional[Dict[str, Any]] = None
    total_turn_count: int = Field(default=0, ge=0)
    recent_turns: List[SessionTurn] = Field(default_factory=list)
    open_tasks: List[str] = Field(default_factory=list)
    active_state: Optional[Dict[str, Any]] = None
    recent_tool_events: List[SessionToolEvent] = Field(default_factory=list)
    prompt_context_id: Optional[str] = None
    checkpoint_id: Optional[str] = None
    seed_bundle_id: Optional[str] = None
    archive_refs: List[str] = Field(default_factory=list)
