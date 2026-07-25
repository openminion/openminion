from pydantic import BaseModel, ConfigDict, Field


class ScopedTargetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    scope: str = Field(default="fixture", min_length=1)


class TimeWindowArgs(ScopedTargetArgs):
    since: str = ""
    until: str = ""
    limit: int = Field(default=50, ge=1, le=500)


class GitOpsAppArgs(TimeWindowArgs):
    controller: str = Field(default="argocd", pattern="^(argocd|flux)$")
    cluster: str = Field(min_length=1)
    namespace: str = Field(min_length=1)
    app: str = Field(min_length=1)
