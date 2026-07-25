# ruff: noqa: F403,F405
from .common import *
from .budgets import ContextBudgets

class SkillSnippetRef(BaseModel):
    skill_id: str
    version_hash: Optional[str] = None


class BuildConstraints(BaseModel):
    output_schema: Optional[Dict[str, Any]] = None
    style_overrides: Dict[str, str] = Field(default_factory=dict)
    safety_tags: List[str] = Field(default_factory=list)
    procedure_id: Optional[str] = None
    skill_id: Optional[str] = None
    skill_version_hash: Optional[str] = None
    skill_refs: List[SkillSnippetRef] = Field(default_factory=list)
    context_budget_tier: Optional[ContextBudgetTier] = None
    tool_schemas: List[Dict[str, Any]] = Field(default_factory=list)
    runtime_tool_schemas: List[Dict[str, Any]] = Field(default_factory=list)


class BuildPackRequest(BaseModel):
    session_id: str
    agent_id: str
    purpose: Purpose
    mode_name: Optional[str] = None
    query: str
    provider_pref: Optional[str] = None
    budgets_override: Optional[ContextBudgets] = None
    constraints: Optional[BuildConstraints] = None
    model_hint: Optional[str] = None
    llm_call_id: Optional[str] = None
    introspection_intent: bool = Field(default=False)
    budget_telemetry: Dict[str, Any] = Field(default_factory=dict)
    live_state_overlay: Dict[str, Any] = Field(default_factory=dict)
    phase_hints: Dict[str, Any] = Field(default_factory=dict)
    gateway_system_context: str = ""
    self_awareness: Dict[str, Any] = Field(default_factory=dict)
