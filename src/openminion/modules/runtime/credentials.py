from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Mapping, Protocol

from openminion.base.time import utc_now_iso as _now_iso

if TYPE_CHECKING:
    from openminion.base.config.env import EnvironmentConfig

CredentialScopeKind = Literal[
    "process",
    "profile",
    "agent",
    "tool_family",
]

CredentialSourceKind = Literal[
    "env",
    "secret_ref",
    "profile_override",
]

CredentialRotationPolicy = Literal[
    "static",
    "reload_on_auth_failure",
]

CredentialAccessDecision = Literal[
    "allowed",
    "denied",
]

CredentialRotationTrigger = Literal["auth_invalid"]


CREDENTIAL_SCOPE_KINDS: tuple[CredentialScopeKind, ...] = (
    "process",
    "profile",
    "agent",
    "tool_family",
)

CREDENTIAL_SOURCE_KINDS: tuple[CredentialSourceKind, ...] = (
    "env",
    "secret_ref",
    "profile_override",
)

CREDENTIAL_ROTATION_POLICIES: tuple[CredentialRotationPolicy, ...] = (
    "static",
    "reload_on_auth_failure",
)


@dataclass(frozen=True)
class CredentialRef:
    """Typed reference to a credential — never the value itself."""

    credential_id: str
    scope_kind: CredentialScopeKind
    scope_id: str
    source_kind: CredentialSourceKind
    env_name: str
    rotation_policy: CredentialRotationPolicy


@dataclass(frozen=True)
class CredentialAccessEvent:
    """Typed audit record of one credential boundary crossing.

    Never carries the secret value.
    """

    event_id: str
    credential_id: str
    scope_kind: CredentialScopeKind
    scope_id: str
    access_site: str
    caller_agent_id: str
    caller_profile_id: str
    decision: CredentialAccessDecision
    recorded_at: str


@dataclass(frozen=True)
class CredentialRotationEvent:
    """Typed audit record of one credential rotation reload.

    Never carries the secret value (old or new).
    """

    event_id: str
    credential_id: str
    scope_kind: CredentialScopeKind
    scope_id: str
    trigger: CredentialRotationTrigger
    recorded_at: str


@dataclass(frozen=True)
class CredentialScopeViolation(Exception):
    """Raised when an agent/profile is not permitted to resolve a ref.

    Carries only typed scope metadata — never the secret value.
    """

    credential_id: str
    scope_kind: CredentialScopeKind
    scope_id: str
    caller_agent_id: str
    caller_profile_id: str

    def __post_init__(self) -> None:
        super().__init__(
            (
                "credential_scope_violation:"
                f"credential_id={self.credential_id},"
                f"scope_kind={self.scope_kind},"
                f"scope_id={self.scope_id},"
                f"caller_agent_id={self.caller_agent_id},"
                f"caller_profile_id={self.caller_profile_id}"
            )
        )


class CredentialAuditLog(Protocol):
    """Structural surface the canonical-events stream owner must satisfy."""

    def append(
        self,
        event: CredentialAccessEvent | CredentialRotationEvent,
    ) -> None: ...


class CredentialValueReader(Protocol):
    """Structural surface for the per-source resolver adapter."""

    def resolve(self, ref: "CredentialRef") -> str: ...


_SOURCE_KIND_ROUTING: Mapping[CredentialSourceKind, str] = MappingProxyType(
    {
        "env": "openminion.base.config.env",
        "secret_ref": "openminion.modules.secret.loader",
        "profile_override": "openminion.base.config.runtime.profile",
    }
)


def credential_source_routing() -> Mapping[CredentialSourceKind, str]:
    """Return the frozen source-kind → substrate routing map.

    The returned mapping is a :class:`types.MappingProxyType`; runtime cannot
    re-key it. Used by the closed-set + frozen-dict regression.
    """
    return _SOURCE_KIND_ROUTING


def resolve_credential_ref(
    credential_id: str,
    *,
    scope_kind: CredentialScopeKind,
    scope_id: str,
    source_kind: CredentialSourceKind = "env",
    env_name: str = "",
    rotation_policy: CredentialRotationPolicy = "static",
) -> CredentialRef:
    """Build a typed :class:`CredentialRef`."""
    if source_kind not in CREDENTIAL_SOURCE_KINDS:
        raise ValueError(
            f"unknown credential source_kind: {source_kind!r}; "
            f"must be one of {CREDENTIAL_SOURCE_KINDS!r}"
        )
    if scope_kind not in CREDENTIAL_SCOPE_KINDS:
        raise ValueError(
            f"unknown credential scope_kind: {scope_kind!r}; "
            f"must be one of {CREDENTIAL_SCOPE_KINDS!r}"
        )
    if rotation_policy not in CREDENTIAL_ROTATION_POLICIES:
        raise ValueError(
            f"unknown credential rotation_policy: {rotation_policy!r}; "
            f"must be one of {CREDENTIAL_ROTATION_POLICIES!r}"
        )
    return CredentialRef(
        credential_id=str(credential_id or "").strip(),
        scope_kind=scope_kind,
        scope_id=str(scope_id or "").strip(),
        source_kind=source_kind,
        env_name=str(env_name or "").strip(),
        rotation_policy=rotation_policy,
    )


def assert_credential_scope(
    ref: CredentialRef,
    *,
    caller_agent_id: str,
    caller_profile_id: str,
) -> None:
    """Raise :class:`CredentialScopeViolation` on scope mismatch."""
    agent_id = str(caller_agent_id or "").strip()
    profile_id = str(caller_profile_id or "").strip()
    if ref.scope_kind != "process":
        caller_scope_id = (
            agent_id
            if ref.scope_kind == "agent"
            else profile_id
            if ref.scope_kind in {"profile", "tool_family"}
            else None
        )
        if caller_scope_id and caller_scope_id == ref.scope_id:
            return
        raise CredentialScopeViolation(
            credential_id=ref.credential_id,
            scope_kind=ref.scope_kind,
            scope_id=ref.scope_id,
            caller_agent_id=agent_id,
            caller_profile_id=profile_id,
        )


def redacted_credential_ref(ref: CredentialRef) -> str:
    """Render an audit/log/event-safe placeholder for the credential."""
    return (
        "<credential "
        f"id={ref.credential_id} "
        f"scope={ref.scope_kind}:{ref.scope_id} "
        f"source={ref.source_kind} "
        f"rotation={ref.rotation_policy}>"
    )


def record_credential_access_event(
    ref: CredentialRef,
    *,
    access_site: str,
    caller_agent_id: str,
    caller_profile_id: str,
    decision: CredentialAccessDecision,
    audit_log: CredentialAuditLog,
) -> CredentialAccessEvent:
    """Emit a :class:`CredentialAccessEvent` to the audit log."""
    site = str(access_site or "").strip()
    if not site:
        raise ValueError(
            "access_site must be a caller-declared static label; "
            "the seam never synthesizes it."
        )
    if decision not in ("allowed", "denied"):
        raise ValueError(
            f"unknown credential access decision: {decision!r}; "
            "must be 'allowed' or 'denied'."
        )
    event = CredentialAccessEvent(
        event_id=str(uuid.uuid4()),
        credential_id=ref.credential_id,
        scope_kind=ref.scope_kind,
        scope_id=ref.scope_id,
        access_site=site,
        caller_agent_id=str(caller_agent_id or "").strip(),
        caller_profile_id=str(caller_profile_id or "").strip(),
        decision=decision,
        recorded_at=_now_iso(),
    )
    audit_log.append(event)
    return event


def resolve_credential_env_value(
    ref: CredentialRef,
    *,
    caller_agent_id: str,
    caller_profile_id: str,
    access_site: str,
    audit_log: CredentialAuditLog,
    env: "EnvironmentConfig | Mapping[str, object] | None" = None,
) -> str:
    """Resolve an env-backed credential after scope and audit enforcement."""
    from openminion.base.config.env import resolve_environment_config

    if ref.source_kind != "env":
        raise ValueError(
            "resolve_credential_env_value only resolves env-source refs; "
            f"received source_kind={ref.source_kind!r}."
        )
    assert_credential_scope(
        ref,
        caller_agent_id=caller_agent_id,
        caller_profile_id=caller_profile_id,
    )
    record_credential_access_event(
        ref,
        access_site=access_site,
        caller_agent_id=caller_agent_id,
        caller_profile_id=caller_profile_id,
        decision="allowed",
        audit_log=audit_log,
    )
    return resolve_environment_config(env=env).get(ref.env_name, "")


def reload_credential_after_auth_failure(
    ref: CredentialRef,
    *,
    audit_log: CredentialAuditLog,
) -> CredentialRotationEvent:
    """Owned reload path for the typed ``AUTH_INVALID`` shaping."""
    if ref.rotation_policy != "reload_on_auth_failure":
        raise ValueError(
            "reload_credential_after_auth_failure called on credential with "
            f"rotation_policy={ref.rotation_policy!r}; only "
            "'reload_on_auth_failure' credentials may reload here."
        )
    event = CredentialRotationEvent(
        event_id=str(uuid.uuid4()),
        credential_id=ref.credential_id,
        scope_kind=ref.scope_kind,
        scope_id=ref.scope_id,
        trigger="auth_invalid",
        recorded_at=_now_iso(),
    )
    audit_log.append(event)
    return event


@dataclass
class InMemoryCredentialAuditLog:
    """Simple FIFO-per-request audit log used by tests and default wiring."""

    events: list[CredentialAccessEvent | CredentialRotationEvent] = field(
        default_factory=list
    )

    def append(
        self,
        event: CredentialAccessEvent | CredentialRotationEvent,
    ) -> None:
        self.events.append(event)

    def access_events(self) -> tuple[CredentialAccessEvent, ...]:
        return tuple(
            event for event in self.events if isinstance(event, CredentialAccessEvent)
        )

    def rotation_events(self) -> tuple[CredentialRotationEvent, ...]:
        return tuple(
            event for event in self.events if isinstance(event, CredentialRotationEvent)
        )


__all__ = (
    "CREDENTIAL_ROTATION_POLICIES",
    "CREDENTIAL_SCOPE_KINDS",
    "CREDENTIAL_SOURCE_KINDS",
    "CredentialAccessDecision",
    "CredentialAccessEvent",
    "CredentialAuditLog",
    "CredentialRef",
    "CredentialRotationEvent",
    "CredentialRotationPolicy",
    "CredentialRotationTrigger",
    "CredentialScopeKind",
    "CredentialScopeViolation",
    "CredentialSourceKind",
    "CredentialValueReader",
    "InMemoryCredentialAuditLog",
    "assert_credential_scope",
    "credential_source_routing",
    "record_credential_access_event",
    "redacted_credential_ref",
    "reload_credential_after_auth_failure",
    "resolve_credential_env_value",
    "resolve_credential_ref",
)
