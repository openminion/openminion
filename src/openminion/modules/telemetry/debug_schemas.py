from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TELEMETRY_DEBUG_SCHEMA_V1 = "openminion.telemetry_debug.v1"
TELEMETRY_EXPORT_SMOKE_SCHEMA_V1 = "openminion.telemetry_export_smoke.v1"
TELEMETRY_RETENTION_PLAN_SCHEMA_V1 = "openminion.telemetry_retention_plan.v1"
TELEMETRY_CORRELATION_REPORT_SCHEMA_V1 = "openminion.telemetry_correlation_report.v1"
TELEMETRY_TIMING_REPORT_SCHEMA_V1 = "openminion.telemetry_timing_report.v1"


@dataclass(frozen=True)
class TelemetryDebugDiagnostic:
    code: str
    severity: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "details": self.details}


@dataclass(frozen=True)
class TelemetryDebugSelection:
    kind: str
    source: str
    selected_invocation_id: str | None
    high_water_storage_sequence: int | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "selected_invocation_id": self.selected_invocation_id,
            "high_water_storage_sequence": self.high_water_storage_sequence,
        }


@dataclass(frozen=True)
class TelemetryDebugUsage:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost_usd: str | None
    llm_call_count: int
    calls_with_usage: int
    complete: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost_usd,
            "llm_call_count": self.llm_call_count,
            "calls_with_usage": self.calls_with_usage,
            "complete": self.complete,
        }


@dataclass(frozen=True)
class TelemetryDebugInvocation:
    invocation_id: str
    outcome: str
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "outcome": self.outcome,
            "started_at": self.started_at,
            "terminal_at": self.terminal_at,
            "session_ids": self.session_ids,
            "agent_ids": self.agent_ids,
            "execution_count": self.execution_count,
            "trace_count": self.trace_count,
            "duration_ms": self.duration_ms,
            "provider": self.provider,
            "model": self.model,
            "usage": self.usage.to_dict() if self.usage else None,
        }


@dataclass(frozen=True)
class TelemetryDebugLinks:
    commands: list[str] = field(default_factory=list)
    trace_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"commands": self.commands, "trace_paths": self.trace_paths}


@dataclass(frozen=True)
class TelemetryDebugExportHealth:
    state: str
    enabled: bool
    endpoint_configured: bool
    protocol: str | None
    sampling_rate: float | None
    queue: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "enabled": self.enabled,
            "endpoint_configured": self.endpoint_configured,
            "protocol": self.protocol,
            "sampling_rate": self.sampling_rate,
            "queue": self.queue,
        }


@dataclass(frozen=True)
class TelemetryDebugError:
    code: str
    category: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "category": self.category}


@dataclass(frozen=True)
class TelemetryDebugReport:
    status: str
    selection: TelemetryDebugSelection | None
    invocation: TelemetryDebugInvocation | None
    diagnostics: list[TelemetryDebugDiagnostic]
    links: TelemetryDebugLinks
    export_health: TelemetryDebugExportHealth
    error: TelemetryDebugError | None = None
    schema_version: str = TELEMETRY_DEBUG_SCHEMA_V1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "selection": self.selection.to_dict() if self.selection else None,
            "invocation": self.invocation.to_dict() if self.invocation else None,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "links": self.links.to_dict(),
            "export_health": self.export_health.to_dict(),
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass(frozen=True)
class TelemetryExportSmokeReport:
    status: str
    configuration: dict[str, Any]
    probe: dict[str, Any]
    proof: dict[str, bool]
    diagnostics: list[TelemetryDebugDiagnostic]
    error: TelemetryDebugError | None = None
    schema_version: str = TELEMETRY_EXPORT_SMOKE_SCHEMA_V1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "configuration": self.configuration,
            "probe": self.probe,
            "proof": self.proof,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "error": self.error.to_dict() if self.error else None,
        }


@dataclass(frozen=True)
class TelemetryRetentionPlan:
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "selector": self.selector,
            "created_at": self.created_at,
            "high_water_storage_sequence": self.high_water_storage_sequence,
            "candidates": self.candidates,
            "exclusions": self.exclusions,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "error": self.error,
            "apply_supported": self.apply_supported,
            "apply_blocker": self.apply_blocker,
        }


@dataclass(frozen=True)
class TelemetryCorrelationReport:
    status: str
    scope: dict[str, Any] | None
    invocation_count: int
    fields: list[dict[str, Any]]
    diagnostics: list[TelemetryDebugDiagnostic]
    error: dict[str, str] | None = None
    schema_version: str = TELEMETRY_CORRELATION_REPORT_SCHEMA_V1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "scope": self.scope,
            "invocation_count": self.invocation_count,
            "fields": self.fields,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "error": self.error,
        }


@dataclass(frozen=True)
class TelemetryTimingReport:
    status: str
    scope: dict[str, Any] | None
    invocation_count: int
    event_count: int
    phases: list[dict[str, Any]]
    provider_models: list[dict[str, Any]]
    diagnostics: list[TelemetryDebugDiagnostic]
    error: dict[str, str] | None = None
    schema_version: str = TELEMETRY_TIMING_REPORT_SCHEMA_V1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "scope": self.scope,
            "invocation_count": self.invocation_count,
            "event_count": self.event_count,
            "phases": self.phases,
            "provider_models": self.provider_models,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "error": self.error,
        }
