from typing import Literal

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


class ProcessArgs(ObservationArgs):
    pid: int = Field(ge=1, le=4_194_304)


class PortOwnerArgs(ObservationArgs):
    port: int = Field(ge=1, le=65_535)
    protocol: Literal["tcp", "udp"] = "tcp"


class ProfileArgs(ObservationArgs):
    profile_id: str = Field(
        description="Closed runtime-owned profile such as disk.usage or memory.usage."
    )


class JobArgs(StrictArgs):
    job_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)


class CommandPlanArgs(TargetArgs):
    argv: tuple[str, ...] = Field(min_length=1)
    cwd: str = ""
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    idempotency_key: str = ""


class CommandRunArgs(StrictArgs):
    plan_id: str = Field(min_length=1)
    plan_hash: str = Field(min_length=64, max_length=64)


class FileReadArgs(TargetArgs):
    path: str = Field(min_length=1)
    max_bytes: int = Field(default=16_384, ge=1, le=131_072)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
