"""Typed rows and request schemas for modules tool authoring."""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AuthoredToolTier = Literal["experimental", "trusted"]
AuthoredToolScope = Literal["READ_ONLY", "WRITE_SAFE", "POWER_USER", "UI_AUTOMATION"]

_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class _StrictToolAuthoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolAuthorArgs(_StrictToolAuthoringModel):
    """Agent-provided source/test input for a new authored-tool draft."""

    name: str = Field(..., min_length=1, max_length=64, pattern=_NAME_PATTERN)
    description: str = Field(..., min_length=1, max_length=1_000)
    source_code: str = Field(..., min_length=1, max_length=100_000)
    unit_tests_source: str = Field(..., min_length=1, max_length=100_000)
    args_schema: dict[str, Any] = Field(default_factory=dict)
    returns_schema: dict[str, Any] = Field(default_factory=dict)
    requirements: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    proposed_scope_tier: AuthoredToolScope = "POWER_USER"


class ToolInspectArgs(_StrictToolAuthoringModel):
    """Draft or ad-hoc authored-tool inspection request."""

    draft_id: str | None = Field(default=None, min_length=1, max_length=256)
    source_code: str | None = Field(default=None, min_length=1, max_length=100_000)
    unit_tests_source: str | None = Field(
        default=None, min_length=1, max_length=100_000
    )
    target_scope_tier: AuthoredToolScope = "POWER_USER"
    run_tests: bool = True

    @model_validator(mode="after")
    def _require_draft_or_source(self) -> "ToolInspectArgs":
        if self.draft_id or self.source_code:
            return self
        raise ValueError(  # allow-bare-raise: pydantic validator must raise a standard validation error
            "one of draft_id or source_code is required"
        )


class ToolRegisterArgs(_StrictToolAuthoringModel):
    """Persist an inspected draft into the runtime registry."""

    draft_id: str = Field(..., min_length=1, max_length=256)
    force: bool = False


class ToolLibraryListArgs(_StrictToolAuthoringModel):
    """Filter arguments for authored-tool library listing."""

    tier: Literal["experimental", "trusted", "all"] = "all"
    include_removed: bool = False


class ToolGetArgs(_StrictToolAuthoringModel):
    """Fetch one authored tool by registered runtime name."""

    tool_name: str = Field(..., min_length=1, max_length=256)


@dataclass(frozen=True)
class ToolDraftRow:
    draft_id: str
    local_name: str
    description: str
    source_code: str
    unit_tests_source: str
    args_schema_json: str
    returns_schema_json: str
    requirements_json: str
    dependencies_json: str
    proposed_scope_tier: str | None
    status: str
    inspect_result_json: str | None
    created_at: str
    created_by_agent_id: str | None
    created_by_session_id: str | None


@dataclass(frozen=True)
class AuthoredToolRow:
    tool_name: str
    local_name: str
    version_number: int
    version_hash: str
    source_code: str
    unit_tests_source: str
    args_schema_json: str
    returns_schema_json: str
    description: str
    dependencies_json: str
    tier: str
    min_scope: str
    policy_grant_id: str | None
    created_at: str
    updated_at: str
    created_by_agent_id: str | None
    promoted_at: str | None
    promoted_by: str | None
    success_count: int
    failure_count: int
    last_invocation_at: str | None
    removed_at: str | None
    removed_by: str | None


@dataclass(frozen=True)
class AuthoredToolAuditEventRow:
    event_id: str
    timestamp: str
    event_type: str
    target_kind: str
    target_id: str
    agent_id: str | None
    session_id: str | None
    version_hash: str | None
    details_json: str
