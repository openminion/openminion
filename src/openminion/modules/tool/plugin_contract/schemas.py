# ruff: noqa: F403,F405
from .common import *


class ToolCapabilities(BaseModel):
    """Policy-relevant capability metadata attached to each tool."""

    model_config = ConfigDict(extra="forbid")
    risk_level: Literal["low", "med", "high"] = "low"
    requires_network: bool = False
    requires_filesystem: bool = False
    supports_streaming: bool = False
    supports_idempotency: bool = False
    time_sensitive: bool = False
    side_effects: Literal["none", "local", "remote", "external_account"] = "none"
    approval_required_for: tuple[str, ...] = ()
    result_contract: str | None = None
    timeout_policy: str | None = None
    audit_events: tuple[str, ...] = ()


class HealthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    details: dict[str, Any] = Field(default_factory=dict)


class RiskSpec(BaseModel):
    """Method-level risk annotation used by policy engines."""

    model_config = ConfigDict(extra="forbid")
    risk_class: RiskClass
    side_effects: Literal["none", "local", "remote", "external_account"] = "none"
    reversibility: RiskReversibility = "unknown"
    default_confirm: bool = False
    sensitive_targets: list[dict[str, Any] | str] = Field(default_factory=list)


class MethodSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method_name: str
    args_schema: dict[str, Any] = Field(default_factory=dict)
    return_schema: Optional[dict[str, Any]] = None
    description: Optional[str] = None
    risk_spec: Optional[RiskSpec] = None


class ToolSchemaBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    description: Optional[str] = None
    methods: list[MethodSchema] = Field(default_factory=list)
    capabilities: ToolCapabilities = Field(default_factory=ToolCapabilities)


class ToolDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plugin_id: str
    plugin_version: str
    tool: str
    methods: list[str] = Field(default_factory=list)
    capabilities: ToolCapabilities = Field(default_factory=ToolCapabilities)
