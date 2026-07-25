# ruff: noqa: F403,F405
from .common import *


class TraceContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    agent_id: Optional[str] = None
    task_id: Optional[str] = None


class RequestBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_ms: int = 30000
    max_tokens: int = 1024
    max_cost: Optional[float] = None


class RuntimeLLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    purpose: str = Field(default="act", min_length=1)
    messages: list[Message] = Field(default_factory=list)
    output_schema: Optional[dict[str, Any]] = None
    required_capabilities: list[ProviderCapabilityName] = Field(default_factory=list)
    constraints: Optional[dict[str, Any]] = None
    budget: RequestBudget = Field(default_factory=RequestBudget)
    trace: TraceContext = Field(default_factory=TraceContext)
    metadata: dict[str, Any] = Field(default_factory=dict)
