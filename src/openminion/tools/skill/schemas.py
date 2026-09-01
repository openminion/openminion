from pydantic import BaseModel, ConfigDict, Field


class SkillIngestArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Human-readable name for the skill",
    )
    markdown: str = Field(
        ...,
        min_length=1,
        max_length=500_000,
        description="Skill definition in Markdown format",
    )
    scope: str = Field(
        default="agent",
        max_length=64,
        description="Skill scope: 'agent' (default) or 'user'",
    )
    max_snippet_tokens: int = Field(
        default=500,
        ge=40,
        le=4000,
        description="Max tokens for the rendered snippet returned inline",
    )


class SkillIngestUrlArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="HTTP/HTTPS URL pointing to markdown skill content.",
    )
    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=256,
        description="Optional override for the stored skill name.",
    )
    scope: str = Field(
        default="global",
        max_length=64,
        description="Skill scope: 'global' (default), 'agent', or 'user'.",
    )
    max_snippet_tokens: int = Field(
        default=500,
        ge=40,
        le=4000,
        description="Max tokens for the rendered snippet returned inline.",
    )


class SkillInspectArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    markdown: str = Field(
        ...,
        min_length=1,
        max_length=500_000,
        description="Skill markdown to inspect for safety and risk issues.",
    )


class SkillListArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    scope: str | None = Field(
        default=None,
        max_length=64,
        description="Optional scope filter (for example: agent, user).",
    )
    status: str | None = Field(
        default=None,
        max_length=64,
        description="Optional status filter (draft, verified, blessed).",
    )
    tag: str | None = Field(
        default=None,
        max_length=128,
        description="Optional tag filter.",
    )
    tool: str | None = Field(
        default=None,
        max_length=128,
        description="Optional tool/capability filter.",
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of skills to return.",
    )


class SkillGetArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill_id: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Exact stored skill ID.",
    )
    version_hash: str | None = Field(
        default=None,
        max_length=256,
        description="Optional exact skill version; reuse it when reading resources.",
    )
    resource_path: str | None = Field(
        default=None,
        max_length=1024,
        description=(
            "Optional relative path from the skill's resources list, such as "
            "references/guide.md. When set, returns that resource instead of the package."
        ),
    )
    max_chars: int = Field(
        default=50_000,
        ge=1,
        le=1_000_000,
        description="Maximum resource characters returned when resource_path is set.",
    )


class SkillRemoveArgs(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skill_id: str = Field(..., min_length=1, max_length=256)
    version_hash: str | None = Field(default=None, max_length=256)
