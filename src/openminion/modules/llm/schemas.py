from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .constants import (
    LLM_TOOL_CALL_STATUS_REQUESTED,
)

from .errors import ErrorCode

Role = Literal["system", "user", "assistant", "tool"]
ToolCallStatus = Literal["requested", "parsed", "blocked", "error"]
ImageSourceType = Literal["path", "url", "base64"]
ImageDetailLevel = Literal["auto", "low", "high"]
TotalTokensSource = Literal["provider", "derived"]
PromptBlockKind = Literal[
    "static_prefix",
    "mission_snapshot",
    "budget_telemetry",
    "task_digest",
    "summaries",
    "conversation_summary",
    "active_plan",
    "trailer_feedback",
    "recent_window",
    "retrieval",
    "evidence_refs",
    "turn_input",
]


class TextContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"] = "text"
    text: str = ""
    block_kind: Optional[PromptBlockKind] = None
    cache_eligible: bool = False
    segment_ids: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)


class ImageContentPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["image"] = "image"
    source: ImageSourceType
    mime_type: str = ""
    path: Optional[str] = None
    url: Optional[str] = None
    data_base64: Optional[str] = None
    detail_level: ImageDetailLevel = "auto"
    block_kind: Optional[PromptBlockKind] = None
    cache_eligible: bool = False
    segment_ids: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)


MessageContentPart = Annotated[
    TextContentPart | ImageContentPart,
    Field(discriminator="type"),
]


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role
    content: str = ""
    name: Optional[str] = None
    cache_control: Optional[dict[str, Any]] = None
    content_parts: list[MessageContentPart] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    strict: bool = False


ToolChoice = Literal["auto", "none", "required"] | dict[str, Any]


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Optional[str] = None
    name: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    raw_arguments: Optional[str] = None
    status: ToolCallStatus = LLM_TOOL_CALL_STATUS_REQUESTED
    error: Optional[str] = None


class UsageInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    total_source: Optional[TotalTokensSource] = None
    cached_tokens: Optional[int] = None
    cache_creation_tokens: Optional[int] = None


class ResponseError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Optional[str] = None
    model: Optional[str] = None
    messages: list[Message] = Field(default_factory=list)
    tools: Optional[list[ToolSpec]] = None
    tool_choice: Optional[ToolChoice] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_output_tokens: Optional[int] = None
    stop: Optional[list[str]] = None
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    provider: str
    model: str
    output_text: str = ""
    assistant_messages: list[Message] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    thinking: list[dict[str, Any]] = Field(default_factory=list)
    usage: UsageInfo = Field(default_factory=UsageInfo)
    latency_ms: int = 0
    cost_usd: Optional[float] = None
    finish_reason: str = ""
    provider_raw: Optional[dict[str, Any]] = None
    error: Optional[ResponseError] = None
    pending_turn_context: Optional[dict[str, Any]] = None
    confident_complete: Optional[dict[str, Any]] = None
    finalization_status: Optional[dict[str, Any]] = None
    meta_rule_preference: Optional[dict[str, Any]] = None
    memory_consolidation: Optional[dict[str, Any]] = None
    watch_outcome: Optional[dict[str, Any]] = None
    session_work_summary: Optional[dict[str, Any]] = None
    # model-authored goal declaration. Populated when the
    goal_declaration: Optional[dict[str, Any]] = None
    goal_revision: Optional[dict[str, Any]] = None
    delegation_context: Optional[dict[str, Any]] = None
    delegation_result_summary: Optional[dict[str, Any]] = None
    task_plan: Optional[dict[str, Any]] = None
    task_plan_step_completed: Optional[dict[str, Any]] = None
    task_plan_step_blocked: Optional[dict[str, Any]] = None
    task_plan_revision: Optional[dict[str, Any]] = None
    task_plan_abandoned: Optional[dict[str, Any]] = None
    task_plan_completed: Optional[dict[str, Any]] = None
    # Provider-level telemetry for empty response recovery
    telemetry: Optional[dict[str, Any]] = Field(default_factory=dict)


class LLMStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["delta", "done", "error"]
    delta_text: Optional[str] = None
    tool_call: Optional[ToolCall] = None
    error: Optional[ResponseError] = None
