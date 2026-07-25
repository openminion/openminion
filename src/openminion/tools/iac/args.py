from pydantic import BaseModel, ConfigDict, Field


class ScopedTargetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    scope: str = Field(default="fixture", min_length=1)


class IacWorkspaceArgs(ScopedTargetArgs):
    workspace: str = Field(min_length=1)
    tool: str = Field(default="terraform", pattern="^(terraform|opentofu)$")


class IacPlanArgs(IacWorkspaceArgs):
    plan_id: str = Field(min_length=1)


class IacPlanShowArgs(IacWorkspaceArgs):
    plan_hash: str = Field(min_length=1)
