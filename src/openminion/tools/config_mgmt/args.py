from pydantic import BaseModel, ConfigDict, Field


class ScopedTargetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    scope: str = Field(default="fixture", min_length=1)


class ConfigTargetArgs(ScopedTargetArgs):
    inventory: str = Field(min_length=1)
    host_pattern: str = Field(default="localhost", min_length=1)


class AnsibleCheckArgs(ConfigTargetArgs):
    playbook: str = Field(min_length=1)
    check_mode: bool = True


class SaltTestArgs(ConfigTargetArgs):
    state: str = Field(min_length=1)
    test_mode: bool = True


class SaltJobArgs(ScopedTargetArgs):
    job_id: str = Field(min_length=1)
