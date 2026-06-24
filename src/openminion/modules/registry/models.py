from __future__ import annotations

from openminion.base.time import utc_now_iso as iso_now  # noqa: F401

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Tier = Literal["low", "standard", "high"]
Transport = Literal["inproc", "uds", "http"]
AuthMode = Literal["none", "api_key", "jwt", "mtls"]
StatusState = Literal["unknown", "healthy", "degraded", "offline"]
RegistrySource = Literal["manifest", "runtime", "builtin"]

TIER_ORDER: dict[Tier, int] = {"low": 0, "standard": 1, "high": 2}


def _dedupe_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


class ErrorInfo(BaseModel):
    code: str
    message: str


class LoadInfo(BaseModel):
    inflight: int = 0
    qps: float = 0.0


class Capability(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    methods: list[str] = Field(default_factory=list)
    input_schema_ref: str | None = None
    output_schema_ref: str | None = None
    quality_tier: Tier | None = None
    cost_tier: Tier | None = None
    latency_hint_ms: int | None = None

    @field_validator("name")
    @classmethod
    def _name_required(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("capability name must be non-empty")
        return text

    @field_validator("methods")
    @classmethod
    def _normalize_methods(cls, value: list[str]) -> list[str]:
        return _dedupe_texts(value)

    @field_validator("latency_hint_ms")
    @classmethod
    def _latency_non_negative(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("latency_hint_ms must be >= 0")
        return value


class TransportEndpoint(BaseModel):
    model_config = ConfigDict(extra="allow")

    endpoint_id: str
    transport: Transport
    address: str
    priority: int = 100
    enabled: bool = True
    meta: dict[str, Any] | None = None

    @field_validator("endpoint_id", "address")
    @classmethod
    def _required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("field must be non-empty")
        return text


class AuthPolicy(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: AuthMode = "none"
    secret_ref: str | None = None
    audience: str | None = None
    meta: dict[str, Any] | None = None


class AgentDescriptor(BaseModel):
    model_config = ConfigDict(extra="allow")

    agent_id: str
    display_name: str
    version: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    endpoints: list[TransportEndpoint] = Field(default_factory=list)
    default_endpoint: str | None = None
    auth: AuthPolicy = Field(default_factory=AuthPolicy)
    limits: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None

    @field_validator("agent_id", "display_name", "version")
    @classmethod
    def _required_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("field must be non-empty")
        return text

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, value: list[str]) -> list[str]:
        return _dedupe_texts(value)

    @model_validator(mode="after")
    def _validate_endpoints(self) -> "AgentDescriptor":
        if not self.endpoints:
            raise ValueError("agent descriptor requires at least one endpoint")

        endpoint_ids: set[str] = set()
        for endpoint in self.endpoints:
            if endpoint.endpoint_id in endpoint_ids:
                raise ValueError(f"duplicate endpoint_id: {endpoint.endpoint_id}")
            endpoint_ids.add(endpoint.endpoint_id)

        if self.default_endpoint and self.default_endpoint not in endpoint_ids:
            raise ValueError("default_endpoint must reference an endpoint_id")
        return self

    def supports_method(self, method: str) -> bool:
        return any(method in cap.methods for cap in self.capabilities)

    def capability_matches_method(self, method: str) -> list[Capability]:
        return [cap for cap in self.capabilities if method in cap.methods]


class AgentStatus(BaseModel):
    model_config = ConfigDict(extra="allow")

    agent_id: str
    state: StatusState = "unknown"
    last_heartbeat_at: str | None = None
    last_error: ErrorInfo | None = None
    current_load: LoadInfo | None = None


class ResolvedRoute(BaseModel):
    agent_id: str
    method: str | None = None
    endpoint: TransportEndpoint
    auth: AuthPolicy
    selection_reason: str


class ResolveConstraints(BaseModel):
    require_tags: list[str] = Field(default_factory=list)
    avoid_tags: list[str] = Field(default_factory=list)
    min_quality_tier: Tier | None = None
    max_cost_tier: Tier | None = None
    prefer_transport: Transport | None = None
    require_transport: Transport | None = None
    agent_allowlist: list[str] = Field(default_factory=list)

    @field_validator("require_tags", "avoid_tags", "agent_allowlist")
    @classmethod
    def _dedupe_text_list(cls, value: list[str]) -> list[str]:
        return _dedupe_texts(value)

    @classmethod
    def from_any(
        cls, raw: dict[str, Any] | "ResolveConstraints" | None
    ) -> "ResolveConstraints":
        if raw is None:
            return cls()
        if isinstance(raw, cls):
            return raw
        return cls.model_validate(raw)


class MethodIndexRow(BaseModel):
    method: str
    agent_id: str
    quality_tier: Tier | None = None
    cost_tier: Tier | None = None
    latency_hint_ms: int | None = None


class AgentRecord(BaseModel):
    agent_id: str
    descriptor: AgentDescriptor
    source: RegistrySource
    updated_at: str


def extract_method_rows(descriptor: AgentDescriptor) -> list[MethodIndexRow]:
    rows: dict[str, MethodIndexRow] = {}
    for capability in descriptor.capabilities:
        for method in capability.methods:
            if method in rows:
                continue
            rows[method] = MethodIndexRow(
                method=method,
                agent_id=descriptor.agent_id,
                quality_tier=capability.quality_tier,
                cost_tier=capability.cost_tier,
                latency_hint_ms=capability.latency_hint_ms,
            )
    return sorted(rows.values(), key=lambda row: row.method)


def tier_gte(actual: Tier | None, required: Tier | None) -> bool:
    if required is None:
        return True
    if actual is None:
        return False
    return TIER_ORDER[actual] >= TIER_ORDER[required]


def tier_lte(actual: Tier | None, maximum: Tier | None) -> bool:
    if maximum is None:
        return True
    if actual is None:
        return False
    return TIER_ORDER[actual] <= TIER_ORDER[maximum]
