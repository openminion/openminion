from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Literal, Protocol
from collections.abc import Iterable, Mapping

from openminion.modules.runtime.constants import AUDIT_EVENT_TYPE_PREFIX

AuditEventKind = Literal[
    "tool_invoked",
    "memory_read",
    "memory_mutated",
    "credential_access",
    "policy_decision",
    "intervention_issued",
    "user_data_exported",
    "user_data_erased",
]

AUDIT_EVENT_KINDS: tuple[AuditEventKind, ...] = (
    "tool_invoked",
    "memory_read",
    "memory_mutated",
    "credential_access",
    "policy_decision",
    "intervention_issued",
    "user_data_exported",
    "user_data_erased",
)

AuditRuntimeSource = Literal[
    "canonical_tool_event",
    "gws_credential_event",
    "memory_context_event",
    "memory_writer_event",
    "executor_security_event",
    "intervention_recorded_event",
    "memory_export_event",
    "memory_erase_event",
]

AUDIT_RUNTIME_SOURCES: tuple[AuditRuntimeSource, ...] = (
    "canonical_tool_event",
    "gws_credential_event",
    "memory_context_event",
    "memory_writer_event",
    "executor_security_event",
    "intervention_recorded_event",
    "memory_export_event",
    "memory_erase_event",
)


@dataclass(frozen=True)
class AuditEvent:
    """Typed compliance audit event."""

    kind: AuditEventKind
    actor_ref: str
    target_ref: str
    timestamp: datetime
    trace_id: str
    session_id: str
    policy_ref: str
    artifact_refs: tuple[str, ...]
    redaction_mode: str
    immutable: bool


@dataclass(frozen=True)
class AuditRetentionPolicy:
    """Typed retention policy keyed by ``AuditEventKind``."""

    durations: Mapping[AuditEventKind, timedelta]
    holds: frozenset[AuditEventKind]
    erasure_eligible: frozenset[AuditEventKind]


@dataclass(frozen=True)
class AuditQueryRequest:
    """Typed filter over the audit substrate."""

    kind: frozenset[AuditEventKind] = frozenset()
    actor_ref: str | None = None
    target_class: str | None = None
    time_range: tuple[datetime, datetime] | None = None


@dataclass(frozen=True)
class AuditQueryResult:
    """Typed cursor over matching audit records.

    Deterministic ordering: timestamp ASC, then ``trace_id`` lexicographic.
    The ordering is fixed; callers cannot re-key it.
    """

    records: tuple[AuditEvent, ...]


@dataclass(frozen=True)
class RetentionApplyResult:
    """Typed result of a retention sweep."""

    erased_event_ids: tuple[str, ...]
    retained_event_ids: tuple[str, ...]


class AuditAppendOnlyViolation(RuntimeError):
    """Raised when a caller attempts to mutate or delete a recorded event."""


_SOURCE_TO_KIND: Mapping[AuditRuntimeSource, AuditEventKind] = MappingProxyType(
    {
        "canonical_tool_event": "tool_invoked",
        "gws_credential_event": "credential_access",
        "memory_context_event": "memory_read",
        "memory_writer_event": "memory_mutated",
        "executor_security_event": "policy_decision",
        "intervention_recorded_event": "intervention_issued",
        "memory_export_event": "user_data_exported",
        "memory_erase_event": "user_data_erased",
    }
)


def audit_event_kind_for_source(source: AuditRuntimeSource) -> AuditEventKind:
    """Return the ``AuditEventKind`` projected from ``source``."""

    return _SOURCE_TO_KIND[source]


_DEFAULT_DURATIONS: Mapping[AuditEventKind, timedelta] = MappingProxyType(
    {
        "tool_invoked": timedelta(days=365),
        "memory_read": timedelta(days=365),
        "memory_mutated": timedelta(days=365 * 2),
        "credential_access": timedelta(days=365 * 2),
        "policy_decision": timedelta(days=365 * 7),
        "intervention_issued": timedelta(days=365 * 7),
        "user_data_exported": timedelta(days=365 * 7),
        "user_data_erased": timedelta(days=365 * 7),
    }
)

DEFAULT_AUDIT_RETENTION_POLICY: AuditRetentionPolicy = AuditRetentionPolicy(
    durations=_DEFAULT_DURATIONS,
    holds=frozenset({"policy_decision", "intervention_issued", "user_data_erased"}),
    erasure_eligible=frozenset(
        {
            "tool_invoked",
            "memory_read",
            "memory_mutated",
            "credential_access",
            "user_data_exported",
        }
    ),
)


SOC2_CHANGE_DECISION_KINDS: frozenset[AuditEventKind] = frozenset(
    {"tool_invoked", "policy_decision", "intervention_issued"}
)
GDPR_ERASURE_ACCESS_KINDS: frozenset[AuditEventKind] = frozenset(
    {"user_data_exported", "user_data_erased", "memory_read"}
)
HIPAA_SENSITIVE_ACCESS_KINDS: frozenset[AuditEventKind] = frozenset(
    {"credential_access", "memory_read"}
)


class AuditLog(Protocol):
    """Append-only audit-log adapter."""

    def append(self, event: AuditEvent) -> str: ...

    def iter_records(self) -> Iterable[tuple[str, AuditEvent]]: ...

    def delete(self, audit_event_id: str) -> None: ...


@dataclass
class InMemoryAuditLog:
    """In-memory append-only audit log."""

    _records: dict[str, AuditEvent] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)
    _next_id: int = 0
    _sweeping: bool = False

    def append(self, event: AuditEvent) -> str:
        self._next_id += 1
        audit_event_id = f"audit-{self._next_id}"
        if audit_event_id in self._records:
            # Defensive: id collisions would imply a non-monotonic
            # counter — treat as an append-only violation.
            raise AuditAppendOnlyViolation(
                f"audit-event id collision: {audit_event_id}"
            )
        self._records[audit_event_id] = event
        self._order.append(audit_event_id)
        return audit_event_id

    def iter_records(self) -> Iterable[tuple[str, AuditEvent]]:
        for audit_event_id in self._order:
            yield audit_event_id, self._records[audit_event_id]

    def delete(self, audit_event_id: str) -> None:
        if not self._sweeping:
            raise AuditAppendOnlyViolation(
                "delete is only valid from apply_audit_retention_policy; "
                "audit log is otherwise append-only"
            )
        if audit_event_id not in self._records:
            return
        del self._records[audit_event_id]
        self._order.remove(audit_event_id)


def project_runtime_event_to_audit_event(
    source: AuditRuntimeSource,
    *,
    actor_ref: str,
    target_ref: str,
    timestamp: datetime,
    trace_id: str,
    session_id: str,
    policy_ref: str,
    artifact_refs: tuple[str, ...] = (),
    redaction_mode: str = "none",
    immutable: bool = True,
) -> AuditEvent:
    """Project a typed runtime event onto an ``AuditEvent``."""

    if source not in AUDIT_RUNTIME_SOURCES:
        raise ValueError(
            f"unknown runtime source: {source!r}; "
            f"must be one of {AUDIT_RUNTIME_SOURCES}"
        )
    kind = audit_event_kind_for_source(source)
    return AuditEvent(
        kind=kind,
        actor_ref=actor_ref,
        target_ref=target_ref,
        timestamp=timestamp,
        trace_id=trace_id,
        session_id=session_id,
        policy_ref=policy_ref,
        artifact_refs=tuple(artifact_refs),
        redaction_mode=redaction_mode,
        immutable=immutable,
    )


def record_audit_event(event: AuditEvent, *, audit_log: AuditLog) -> str:
    """Append a typed audit event onto the audit log."""

    if event.kind not in AUDIT_EVENT_KINDS:
        raise ValueError(
            f"unknown audit-event kind: {event.kind!r}; "
            f"must be one of {AUDIT_EVENT_KINDS}"
        )
    return audit_log.append(event)


def _matches(request: AuditQueryRequest, event: AuditEvent) -> bool:
    if request.kind and event.kind not in request.kind:
        return False
    if request.actor_ref is not None and event.actor_ref != request.actor_ref:
        return False
    if request.target_class is not None:
        prefix = request.target_class
        # ``target_class`` matches a structural prefix on ``target_ref``;
        # the seam never scans prose. Callers declare the prefix.
        if not event.target_ref.startswith(prefix):
            return False
    if request.time_range is not None:
        start, end = request.time_range
        if not (start <= event.timestamp < end):
            return False
    return True


def query_audit_events(
    request: AuditQueryRequest, *, audit_log: AuditLog
) -> AuditQueryResult:
    """Return matching audit records in deterministic order."""

    matched: list[AuditEvent] = [
        event for _id, event in audit_log.iter_records() if _matches(request, event)
    ]
    matched.sort(key=lambda e: (e.timestamp, e.trace_id))
    return AuditQueryResult(records=tuple(matched))


class _Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True)
class FixedClock:
    """Deterministic clock used by retention sweeps in tests."""

    moment: datetime

    def now(self) -> datetime:
        return self.moment


def apply_audit_retention_policy(
    policy: AuditRetentionPolicy,
    *,
    audit_log: AuditLog,
    clock: _Clock,
) -> RetentionApplyResult:
    """Run a deterministic retention sweep over ``audit_log``."""

    now = clock.now()
    erased: list[str] = []
    retained: list[str] = []
    targets: list[str] = []
    for audit_event_id, event in audit_log.iter_records():
        if event.kind in policy.holds:
            retained.append(audit_event_id)
            continue
        if event.kind not in policy.erasure_eligible:
            retained.append(audit_event_id)
            continue
        duration = policy.durations.get(event.kind)
        if duration is None:
            retained.append(audit_event_id)
            continue
        if now - event.timestamp >= duration:
            targets.append(audit_event_id)
        else:
            retained.append(audit_event_id)

    if isinstance(audit_log, InMemoryAuditLog):
        audit_log._sweeping = True
        try:
            for audit_event_id in targets:
                audit_log.delete(audit_event_id)
                erased.append(audit_event_id)
        finally:
            audit_log._sweeping = False
    else:
        for audit_event_id in targets:
            audit_log.delete(audit_event_id)
            erased.append(audit_event_id)

    return RetentionApplyResult(
        erased_event_ids=tuple(erased),
        retained_event_ids=tuple(retained),
    )


def soc2_change_decision_query(
    *,
    actor_ref: str | None = None,
    time_range: tuple[datetime, datetime] | None = None,
) -> AuditQueryRequest:
    """Typed SOC2-style change/decision attribution query template."""

    return AuditQueryRequest(
        kind=SOC2_CHANGE_DECISION_KINDS,
        actor_ref=actor_ref,
        time_range=time_range,
    )


def gdpr_erasure_access_query(
    *,
    target_class: str | None = None,
    time_range: tuple[datetime, datetime] | None = None,
) -> AuditQueryRequest:
    """Typed GDPR-style erasure/access query template."""

    return AuditQueryRequest(
        kind=GDPR_ERASURE_ACCESS_KINDS,
        target_class=target_class,
        time_range=time_range,
    )


def hipaa_sensitive_access_query(
    *,
    actor_ref: str | None = None,
    target_class: str | None = None,
    time_range: tuple[datetime, datetime] | None = None,
) -> AuditQueryRequest:
    """Typed HIPAA-style sensitive-data-access query template."""

    return AuditQueryRequest(
        kind=HIPAA_SENSITIVE_ACCESS_KINDS,
        actor_ref=actor_ref,
        target_class=target_class,
        time_range=time_range,
    )


AUDIT_EVENT_RECORDED_EVENT_TYPE: str = f"{AUDIT_EVENT_TYPE_PREFIX}recorded"


def utcnow() -> datetime:
    """Helper to construct UTC-naive-free timestamps inside the seam."""

    return datetime.now(timezone.utc)


__all__ = [
    "AUDIT_EVENT_KINDS",
    "AUDIT_EVENT_RECORDED_EVENT_TYPE",
    "AUDIT_RUNTIME_SOURCES",
    "AuditAppendOnlyViolation",
    "AuditEvent",
    "AuditEventKind",
    "AuditLog",
    "AuditQueryRequest",
    "AuditQueryResult",
    "AuditRetentionPolicy",
    "AuditRuntimeSource",
    "DEFAULT_AUDIT_RETENTION_POLICY",
    "FixedClock",
    "GDPR_ERASURE_ACCESS_KINDS",
    "HIPAA_SENSITIVE_ACCESS_KINDS",
    "InMemoryAuditLog",
    "RetentionApplyResult",
    "SOC2_CHANGE_DECISION_KINDS",
    "apply_audit_retention_policy",
    "audit_event_kind_for_source",
    "gdpr_erasure_access_query",
    "hipaa_sensitive_access_query",
    "project_runtime_event_to_audit_event",
    "query_audit_events",
    "record_audit_event",
    "soc2_change_decision_query",
    "utcnow",
]
