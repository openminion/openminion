from pydantic import BaseModel, ConfigDict, Field


class ScopedTargetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    scope: str = Field(default="fixture", min_length=1)


class TimeWindowArgs(ScopedTargetArgs):
    since: str = ""
    until: str = ""
    limit: int = Field(default=50, ge=1, le=500)


class K8sBaseArgs(TimeWindowArgs):
    context: str = Field(min_length=1)
    namespace: str = Field(min_length=1)


class WorkloadGetArgs(K8sBaseArgs):
    kind: str = Field(min_length=1)
    name: str = Field(min_length=1)


class WorkloadListArgs(K8sBaseArgs):
    kind: str = "deployment"


class K8sEventsArgs(K8sBaseArgs):
    involved_object: str = ""


class K8sLogsArgs(K8sBaseArgs):
    pod: str = Field(min_length=1)
    container: str = ""
    tail_lines: int = Field(default=100, ge=1, le=1000)


class RolloutStatusArgs(K8sBaseArgs):
    kind: str = Field(default="deployment")
    name: str = Field(min_length=1)
