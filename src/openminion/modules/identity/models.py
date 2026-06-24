from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Purpose = Literal["decide", "plan", "act", "reflect", "summarize", "judge", "validate"]
Verbosity = Literal["terse", "normal", "detailed"]
RiskLevel = Literal["low", "medium", "high"]
ToolUse = Literal["allowed", "restricted", "read_only"]


def _dedupe_text_list(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


class RoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mission: str = Field(..., min_length=1)
    responsibilities: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    domain: list[str] = Field(default_factory=list)
    escalation_rules: list[str] = Field(default_factory=list)

    @field_validator("mission")
    @classmethod
    def _mission_required(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("mission must be non-empty")
        return text

    @field_validator(
        "responsibilities", "hard_constraints", "domain", "escalation_rules"
    )
    @classmethod
    def _normalize_lists(cls, value: list[str]) -> list[str]:
        return _dedupe_text_list(value)


class PersonalitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tone: str = Field(..., min_length=1)
    verbosity: Verbosity = "normal"
    formatting: list[str] = Field(default_factory=list)
    interaction_style: list[str] = Field(default_factory=list)

    @field_validator("tone")
    @classmethod
    def _tone_required(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("tone must be non-empty")
        return text

    @field_validator("formatting", "interaction_style")
    @classmethod
    def _normalize_lists(cls, value: list[str]) -> list[str]:
        return _dedupe_text_list(value)


class RiskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_level: RiskLevel = "medium"
    confirm_before: list[str] = Field(default_factory=list)
    auto_proceed_rules: list[str] = Field(default_factory=list)

    @field_validator("confirm_before", "auto_proceed_rules")
    @classmethod
    def _normalize_lists(cls, value: list[str]) -> list[str]:
        return _dedupe_text_list(value)


class ToolPostureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_use: ToolUse = "restricted"
    sandbox_root: str | None = None
    blocked_patterns: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)

    @field_validator("blocked_patterns", "allowed_tools")
    @classmethod
    def _normalize_lists(cls, value: list[str]) -> list[str]:
        return _dedupe_text_list(value)


class SkillPostureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    always_active: list[str] = Field(default_factory=list)
    query_activated: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    max_skill_tokens: int = Field(default=300, ge=1)

    @field_validator("always_active", "query_activated", "excluded")
    @classmethod
    def _normalize_lists(cls, value: list[str]) -> list[str]:
        return _dedupe_text_list(value)


class AgentProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    profile_revision: int = Field(..., ge=1)
    inherits: str | None = None
    role: RoleSpec
    personality: PersonalitySpec
    risk: RiskSpec
    tool_posture: ToolPostureSpec
    skill_posture: SkillPostureSpec | None = None
    llm_policy_ref: str | None = None
    allowed_capabilities: list[str] | None = None
    meta: dict[str, Any] | None = None

    @field_validator("agent_id", "display_name")
    @classmethod
    def _required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("field must be non-empty")
        return text

    @field_validator("inherits", "llm_policy_ref")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @field_validator("allowed_capabilities")
    @classmethod
    def _normalize_optional_caps(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _dedupe_text_list(value)


class RoleSpecInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mission: str | None = None
    responsibilities: list[str] | None = None
    hard_constraints: list[str] | None = None
    domain: list[str] | None = None
    escalation_rules: list[str] | None = None


class PersonalitySpecInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tone: str | None = None
    verbosity: Verbosity | None = None
    formatting: list[str] | None = None
    interaction_style: list[str] | None = None


class RiskSpecInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_level: RiskLevel | None = None
    confirm_before: list[str] | None = None
    auto_proceed_rules: list[str] | None = None


class ToolPostureSpecInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_use: ToolUse | None = None
    sandbox_root: str | None = None
    blocked_patterns: list[str] | None = None
    allowed_tools: list[str] | None = None


class SkillPostureSpecInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    always_active: list[str] | None = None
    query_activated: list[str] | None = None
    excluded: list[str] | None = None
    max_skill_tokens: int | None = None


class AgentProfileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str | None = None
    display_name: str | None = None
    profile_revision: int | None = None
    inherits: str | None = None
    role: RoleSpecInput | None = None
    personality: PersonalitySpecInput | None = None
    risk: RiskSpecInput | None = None
    tool_posture: ToolPostureSpecInput | None = None
    skill_posture: SkillPostureSpecInput | None = None
    llm_policy_ref: str | None = None
    allowed_capabilities: list[str] | None = None
    meta: dict[str, Any] | None = None


class AgentProfileSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    display_name: str
    profile_revision: int
    profile_version: str
    updated_at: str


class SnippetBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int = Field(..., ge=1)
    used_tokens: int = Field(..., ge=0)
    max_chars: int = Field(..., ge=1)
    used_chars: int = Field(..., ge=0)


class IdentitySnippet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    purpose: str
    text: str
    profile_version: str
    render_version: str
    budget: SnippetBudget
    sections: dict[str, str] | None = None
    included_fields: list[str] = Field(default_factory=list)
    omitted_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
