# ruff: noqa: F403,F405
from .common import *


class ToolInvocation(BaseModel):
    """Canonical invocation payload accepted by ToolRuntime.invoke."""

    model_config = ConfigDict(extra="forbid")
    invocation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool: str = Field(
        ...,
        min_length=1,
        description="Tool namespace, e.g., 'ssh' or 'browser.pinchtab'",
    )
    method: str = Field(
        ..., min_length=1, description="Method name, e.g., 'exec' or 'snapshot'"
    )
    args: dict[str, Any] = Field(default_factory=dict)
    timeout_s: Optional[float] = Field(
        default=None, description="Per-invocation timeout in seconds"
    )
    idempotency_key: Optional[str] = None
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("timeout_s")
    @classmethod
    def _validate_timeout(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and value <= 0:
            raise ValueError(
                "timeout_s must be > 0"
            )  # allow-bare-raise: pydantic @field_validator body
        return value


class ArtifactRef(BaseModel):
    """Artifact descriptor returned by tools and artifact sinks."""

    model_config = ConfigDict(extra="forbid")
    ref: str
    kind: str
    name: str
    meta: dict[str, Any] = Field(default_factory=dict)


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Uniform result for all plugin methods."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "error"]
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: Optional[ToolError] = None

    @model_validator(mode="after")
    def _validate_error_consistency(self) -> "ToolResult":
        if self.status == "error" and self.error is None:
            raise ValueError(
                "error field must be set when status='error'"
            )  # allow-bare-raise: pydantic @model_validator body
        if self.status == "ok" and self.error is not None:
            raise ValueError(
                "error field must be null when status='ok'"
            )  # allow-bare-raise: pydantic @model_validator body
        return self
