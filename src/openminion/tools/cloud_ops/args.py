from pydantic import BaseModel, ConfigDict, Field


class ScopedTargetArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1)
    scope: str = Field(default="fixture", min_length=1)


class TimeWindowArgs(ScopedTargetArgs):
    since: str = ""
    until: str = ""
    limit: int = Field(default=50, ge=1, le=500)


class SsmInventoryArgs(TimeWindowArgs):
    account_id: str = Field(min_length=1)
    region: str = Field(min_length=1)
    tag_key: str = ""
    tag_value: str = ""


class SsmCommandStatusArgs(ScopedTargetArgs):
    account_id: str = Field(min_length=1)
    region: str = Field(min_length=1)
    command_id: str = Field(min_length=1)
