from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import (
    Any,
    Dict,
    List,
    Literal,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
    TYPE_CHECKING,
)

from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator

if TYPE_CHECKING:  # pragma: no cover - typing-only imports
    from .runtime.plugin import ToolRuntime


PolicyAction = Literal["allow", "deny", "require_confirm"]
RiskClass = Literal[
    "read", "write", "exec", "state_change", "destructive", "financial", "security"
]
RiskReversibility = Literal[
    "reversible", "partially_reversible", "irreversible", "unknown"
]


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
    args: Dict[str, Any] = Field(default_factory=dict)
    timeout_s: Optional[float] = Field(
        default=None, description="Per-invocation timeout in seconds"
    )
    idempotency_key: Optional[str] = None
    tags: Dict[str, str] = Field(default_factory=dict)

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
    meta: Dict[str, Any] = Field(default_factory=dict)


class ToolError(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    retryable: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    """Uniform result for all plugin methods."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "error"]
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)
    artifacts: List[ArtifactRef] = Field(default_factory=list)
    metrics: Dict[str, Any] = Field(default_factory=dict)
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


class HealthStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool
    details: Dict[str, Any] = Field(default_factory=dict)


class RiskSpec(BaseModel):
    """Method-level risk annotation used by policy engines."""

    model_config = ConfigDict(extra="forbid")
    risk_class: RiskClass
    side_effects: Literal["none", "local", "remote", "external_account"] = "none"
    reversibility: RiskReversibility = "unknown"
    default_confirm: bool = False
    sensitive_targets: List[Union[Dict[str, Any], str]] = Field(default_factory=list)


class MethodSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method_name: str
    args_schema: Dict[str, Any] = Field(default_factory=dict)
    return_schema: Optional[Dict[str, Any]] = None
    description: Optional[str] = None
    risk_spec: Optional[RiskSpec] = None


class ToolSchemaBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    description: Optional[str] = None
    methods: List[MethodSchema] = Field(default_factory=list)
    capabilities: ToolCapabilities = Field(default_factory=ToolCapabilities)


class ToolDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plugin_id: str
    plugin_version: str
    tool: str
    methods: List[str] = Field(default_factory=list)
    capabilities: ToolCapabilities = Field(default_factory=ToolCapabilities)


@dataclass
class PolicyDecision:
    action: PolicyAction
    reason: str = ""
    code: str = "POLICY_DENIED"
    details: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ArtifactSink(Protocol):
    def put_bytes(
        self,
        *,
        name: str,
        content: bytes,
        kind: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> ArtifactRef: ...


@runtime_checkable
class EventSink(Protocol):
    def emit(self, *, event_name: str, payload: Dict[str, Any]) -> None: ...


@runtime_checkable
class PolicyHook(Protocol):
    def check(
        self,
        *,
        invocation: ToolInvocation,
        ctx: "ToolContext",
        capabilities: ToolCapabilities,
    ) -> PolicyDecision: ...


@dataclass
class ToolContext:
    """Execution context passed to tool methods."""

    trace_id: str
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    working_dir: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    artifact_sink: Optional[ArtifactSink] = None
    event_sink: Optional[EventSink] = None
    logger: Any = None
    runtime: Optional["ToolRuntime"] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def resolved_logger(self) -> logging.Logger:
        if self.logger is not None:
            return self.logger
        return logging.getLogger("openminion.modules.tool.runtime.plugins")


@runtime_checkable
class ToolMethod(Protocol):
    method_name: str
    args_schema: Dict[str, Any]
    return_schema: Dict[str, Any]

    def run(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult: ...


@runtime_checkable
class ToolDefinition(Protocol):
    name: str
    methods: Dict[str, ToolMethod]
    capabilities: ToolCapabilities

    def schema(self) -> ToolSchemaBundle: ...


@runtime_checkable
class ToolPlugin(Protocol):
    plugin_id: str
    version: str

    def get_tools(self) -> List[ToolDefinition]: ...

    def get_config_schema(self) -> Optional[Dict[str, Any]]: ...

    def validate_config(self, config: Dict[str, Any]) -> None: ...

    def init(self, runtime: "ToolRuntime") -> None: ...

    def shutdown(self) -> None: ...

    def healthcheck(self) -> HealthStatus: ...


class NullEventSink:
    """No-op event sink used when caller does not provide one."""

    def emit(self, *, event_name: str, payload: Dict[str, Any]) -> None:
        del event_name, payload


class MemoryEventSink:
    """Simple in-memory sink useful for tests and development."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []

    def emit(self, *, event_name: str, payload: Dict[str, Any]) -> None:
        self.events.append({"event_name": event_name, "payload": dict(payload)})


class MemoryArtifactSink:
    """In-memory artifact sink that also returns stable hash-based references."""

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}

    def put_bytes(
        self,
        *,
        name: str,
        content: bytes,
        kind: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> ArtifactRef:
        sha = hashlib.sha256(content).hexdigest()
        ref = f"artifact:sha256:{sha}"
        self.objects[ref] = content
        now_iso = datetime.now(timezone.utc).isoformat()
        out_meta = dict(meta or {})
        out_meta.setdefault("size", len(content))
        out_meta.setdefault("sha256", sha)
        out_meta.setdefault("created_at", now_iso)
        return ArtifactRef(ref=ref, kind=kind, name=name, meta=out_meta)


class CASArtifactSink:
    """Artifact sink backed by ArtifactCtl canonical CAS storage."""

    def __init__(
        self,
        *,
        artifactctl: Any,
        session_id: str | None = None,
        trace_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._artifactctl = artifactctl
        self._session_id = str(session_id or "").strip() or None
        self._trace_id = str(trace_id or "").strip() or None
        self._agent_id = str(agent_id or "").strip() or None

    def put_bytes(
        self,
        *,
        name: str,
        content: bytes,
        kind: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> ArtifactRef:
        ref = self._artifactctl.ingest_bytes(
            data=content,
            mime=str((meta or {}).get("mime", "") or None) or None,
            original_name=name,
            label=name,
            meta=dict(meta or {}),
            session_id=self._session_id,
            trace_id=self._trace_id,
            agent_id=self._agent_id,
        )
        out_meta = dict(meta or {})
        out_meta.setdefault("size", len(content))
        out_meta.setdefault("sha256", str(getattr(ref, "sha256", "") or ""))
        out_meta.setdefault("created_at", str(getattr(ref, "created_at", "") or ""))
        return ArtifactRef(
            ref=str(getattr(ref, "ref", "") or ""),
            kind=kind,
            name=name,
            meta=out_meta,
        )
