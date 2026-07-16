from pydantic import BaseModel, ConfigDict, Field


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyArgs(StrictArgs):
    pass


class TargetArgs(StrictArgs):
    target_id: str = Field(min_length=1)


class ObservationArgs(TargetArgs):
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)


class ServiceArgs(ObservationArgs):
    service: str = Field(min_length=1)


class LogsArgs(ServiceArgs):
    limit: int = Field(default=100, ge=1, le=500)


class ProfileArgs(ObservationArgs):
    profile_id: str = Field(
        description="Closed runtime-owned profile such as disk.usage or memory.usage."
    )


class JobArgs(StrictArgs):
    job_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
