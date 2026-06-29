from pydantic import BaseModel, ConfigDict, Field


class HostMetricsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str | None = Field(
        default=None,
        description=(
            "Optional path whose containing filesystem should be measured. "
            "Relative paths resolve from the tool workspace."
        ),
    )
    include_disk: bool = Field(
        default=True,
        description="Include disk usage for the requested path and root filesystem.",
    )
    include_memory: bool = Field(
        default=True,
        description="Include host memory totals and available memory when available.",
    )
