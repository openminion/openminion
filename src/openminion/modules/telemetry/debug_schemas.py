from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

TELEMETRY_DEBUG_SCHEMA_V1 = "openminion.telemetry_debug.v1"
TELEMETRY_EXPORT_SMOKE_SCHEMA_V1 = "openminion.telemetry_export_smoke.v1"
TELEMETRY_RETENTION_PLAN_SCHEMA_V1 = "openminion.telemetry_retention_plan.v1"
TELEMETRY_CORRELATION_REPORT_SCHEMA_V1 = "openminion.telemetry_correlation_report.v1"
TELEMETRY_TIMING_REPORT_SCHEMA_V1 = "openminion.telemetry_timing_report.v1"


@dataclass(frozen=True)
class _DictSchema:
    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if "schema_version" not in payload:
            return payload
        return {"schema_version": payload.pop("schema_version"), **payload}


@dataclass(frozen=True)
class TelemetryDebugDiagnostic(_DictSchema):
    code: str
    severity: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TelemetryDebugSelection(_DictSchema):
    kind: str
    source: str
    selected_invocation_id: str | None
    high_water_storage_sequence: int | None


@dataclass(frozen=True)
class TelemetryDebugUsage(_DictSchema):
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost_usd: str | None
    llm_call_count: int
    calls_with_usage: int
    complete: bool


@dataclass(frozen=True)
class TelemetryDebugInvocation(_DictSchema):
    invocation_id: str
    outcome: str
    failure_code: str | None
    started_at: str | None
    terminal_at: str | None
    session_ids: list[str]
    agent_ids: list[str]
    execution_count: int
    trace_count: int | None
    duration_ms: int | None
    provider: str | None
    model: str | None
    usage: TelemetryDebugUsage | None


@dataclass(frozen=True)
class TelemetryDebugLinks(_DictSchema):
    commands: list[str] = field(default_factory=list)
    trace_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TelemetryDebugExportHealth(_DictSchema):
    state: str
    enabled: bool
    endpoint_configured: bool
    protocol: str | None
    sampling_rate: float | None
    queue: dict[str, Any]


@dataclass(frozen=True)
class TelemetryDebugError(_DictSchema):
    code: str
    category: str


@dataclass(frozen=True)
class TelemetryDebugReport(_DictSchema):
    status: str
    selection: TelemetryDebugSelection | None
    invocation: TelemetryDebugInvocation | None
    diagnostics: list[TelemetryDebugDiagnostic]
    links: TelemetryDebugLinks
    export_health: TelemetryDebugExportHealth
    error: TelemetryDebugError | None = None
    schema_version: str = TELEMETRY_DEBUG_SCHEMA_V1


@dataclass(frozen=True)
class TelemetryExportSmokeReport(_DictSchema):
    status: str
    configuration: dict[str, Any]
    probe: dict[str, Any]
    proof: dict[str, bool]
    diagnostics: list[TelemetryDebugDiagnostic]
    error: TelemetryDebugError | None = None
    schema_version: str = TELEMETRY_EXPORT_SMOKE_SCHEMA_V1


@dataclass(frozen=True)
class TelemetryRetentionPlan(_DictSchema):
    status: str
    selector: dict[str, Any] | None
    created_at: str | None
    high_water_storage_sequence: int | None
    candidates: list[dict[str, Any]]
    exclusions: list[dict[str, Any]]
    diagnostics: list[TelemetryDebugDiagnostic]
    error: dict[str, str] | None = None
    apply_supported: bool = False
    apply_blocker: str = "cross_store_retention_fence_unavailable"
    schema_version: str = TELEMETRY_RETENTION_PLAN_SCHEMA_V1


@dataclass(frozen=True)
class TelemetryCorrelationReport(_DictSchema):
    status: str
    scope: dict[str, Any] | None
    invocation_count: int
    fields: list[dict[str, Any]]
    diagnostics: list[TelemetryDebugDiagnostic]
    error: dict[str, str] | None = None
    schema_version: str = TELEMETRY_CORRELATION_REPORT_SCHEMA_V1


@dataclass(frozen=True)
class TelemetryTimingReport(_DictSchema):
    status: str
    scope: dict[str, Any] | None
    invocation_count: int
    event_count: int
    phases: list[dict[str, Any]]
    provider_models: list[dict[str, Any]]
    diagnostics: list[TelemetryDebugDiagnostic]
    error: dict[str, str] | None = None
    schema_version: str = TELEMETRY_TIMING_REPORT_SCHEMA_V1
