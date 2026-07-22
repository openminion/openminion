from dataclasses import dataclass, field
from typing import Any
from collections.abc import Mapping


@dataclass(frozen=True)
class HealthCheckResult:
    name: str
    status: str
    message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthSnapshot:
    status: str
    service: str
    version: str
    checks: list[HealthCheckResult] = field(default_factory=list)

    def add_check(self, result: HealthCheckResult) -> None:
        self.checks.append(result)
        if result.status == "error":
            self.status = "error"
        elif result.status == "degraded" and self.status == "ok":
            self.status = "degraded"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "service": self.service,
            "version": self.version,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status,
                    "message": c.message,
                    "details": c.details,
                }
                for c in self.checks
            ],
        }


@dataclass
class HealthCheck:
    id: str
    status: str
    message: str
    details: dict[str, Any] | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "status": self.status,
            "message": self.message,
        }
        if self.details:
            payload["details"] = self.details
        if self.duration_ms is not None:
            payload["duration_ms"] = int(self.duration_ms)
        return payload


@dataclass
class LifecycleFact:
    component: dict[str, Any]
    latest_event_type: str
    latest_observed_at: str
    source_classification: str | None = None
    last_heartbeat_at: str | None = None
    last_exit_reason: str | None = None
    metrics: dict[str, Any] | None = None

    def observe(
        self,
        *,
        component: Mapping[str, Any],
        event_type: str,
        observed_at: str,
        source_classification: str | None,
        reason: str | None,
        metrics: Mapping[str, Any] | None,
    ) -> None:
        self.component = dict(component)
        self.latest_event_type = str(event_type or "").strip()
        self.latest_observed_at = observed_at
        self.source_classification = source_classification
        self.metrics = dict(metrics or {}) or None
        if self.latest_event_type == "component.heartbeat":
            self.last_heartbeat_at = observed_at
        if self.latest_event_type in {"component.stopped", "component.crashed"}:
            self.last_exit_reason = reason or None
            return
        self.last_exit_reason = None
