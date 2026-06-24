from pydantic import BaseModel, ConfigDict, Field


class IpPublicArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_ms: int | None = Field(
        default=None,
        ge=250,
        le=30000,
        description="Override timeout in milliseconds for public IP lookup.",
    )


class IpLocalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_loopback: bool = Field(
        default=False,
        description="Include loopback addresses like 127.0.0.1 / ::1.",
    )
