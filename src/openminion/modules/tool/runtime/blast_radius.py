import uuid
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Literal, Mapping, Optional, Protocol, Sequence

from openminion.base.time import utc_now_iso as _now_iso
from openminion.modules.runtime.credentials import CredentialRef
from openminion.modules.tool.errors import ToolRuntimeError


ToolBlastRadius = Literal[
    "read_only",
    "local_mutation",
    "remote_mutation",
    "code_execution",
    "process_spawn",
    "network_unbounded",
]

SandboxKind = Literal[
    "none",
    "runtime_local",
    "runtime_bwrap",
    "browser_managed",
    "remote_wrapper",
]

CompositionDecision = Literal[
    "allowed",
    "escalation_required",
    "forbidden",
]


TOOL_BLAST_RADIUSES: tuple[ToolBlastRadius, ...] = (
    "read_only",
    "local_mutation",
    "remote_mutation",
    "code_execution",
    "process_spawn",
    "network_unbounded",
)

SANDBOX_KINDS: tuple[SandboxKind, ...] = (
    "none",
    "runtime_local",
    "runtime_bwrap",
    "browser_managed",
    "remote_wrapper",
)

COMPOSITION_DECISIONS: tuple[CompositionDecision, ...] = (
    "allowed",
    "escalation_required",
    "forbidden",
)


_BLAST_RADIUS_RANK: Mapping[ToolBlastRadius, int] = MappingProxyType(
    {
        "read_only": 0,
        "local_mutation": 1,
        "remote_mutation": 2,
        "code_execution": 3,
        "process_spawn": 4,
        "network_unbounded": 5,
    }
)


def blast_radius_rank(radius: ToolBlastRadius) -> int:
    """Return the typed severity rank for a blast-radius value.

    Used by :func:`classify_composed_blast_radius` and by
    ``max_radius_per_turn`` policy guards. Higher = more permissive.
    """
    if radius not in _BLAST_RADIUS_RANK:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"unknown ToolBlastRadius value: {radius!r}; "
            f"must be one of {TOOL_BLAST_RADIUSES!r}",
            {"radius": str(radius)},
        )
    return _BLAST_RADIUS_RANK[radius]


@dataclass(frozen=True)
class ToolBlastRadiusProfile:
    """Typed per-tool classification."""

    tool_name: str
    blast_radius: ToolBlastRadius
    sandbox_kind: SandboxKind
    credential_ref: Optional[CredentialRef]
    dangerous: bool
    notes: str = ""


@dataclass(frozen=True)
class CompositionPolicy:
    """Typed cross-tool composition gate."""

    policy_id: str
    forbidden_transitions: frozenset[tuple[ToolBlastRadius, ToolBlastRadius]]
    escalation_required_transitions: frozenset[tuple[ToolBlastRadius, ToolBlastRadius]]
    max_radius_per_turn: Optional[ToolBlastRadius]
    frozen: bool = True


@dataclass(frozen=True)
class CompositionBoundaryEvent:
    """Typed audit record of one composition boundary crossing."""

    event_id: str
    policy_id: str
    from_radius: ToolBlastRadius
    to_radius: ToolBlastRadius
    tool_names: tuple[str, ...]
    seam_id: str
    decision: CompositionDecision
    recorded_at: str


class CompositionAuditLog(Protocol):
    """Structural surface the canonical-events stream owner must satisfy."""

    def append(self, event: CompositionBoundaryEvent) -> None: ...


_GENERIC_WRAPPER_FAMILY_FALLBACK: Mapping[str, tuple[ToolBlastRadius, SandboxKind]] = (
    MappingProxyType(
        {
            "gws.call": ("remote_mutation", "remote_wrapper"),
            "mcp:*": ("remote_mutation", "remote_wrapper"),
        }
    )
)


def generic_wrapper_family_fallback() -> Mapping[
    str, tuple[ToolBlastRadius, SandboxKind]
]:
    """Return the frozen generic-wrapper family-fallback map.

    The returned mapping is a :class:`types.MappingProxyType`; runtime
    cannot re-key it. Used by the closed-set + frozen-dict regression.
    """
    return _GENERIC_WRAPPER_FAMILY_FALLBACK


def _resolve_family_fallback(
    tool_name: str,
) -> tuple[ToolBlastRadius, SandboxKind] | None:
    """Look up generic-wrapper family fallback by typed prefix match.

    ``mcp:*`` catches every ``mcp:<tool>`` family entry; explicit names
    like ``gws.call`` match directly. No content scan of tool arguments.
    """
    name = str(tool_name or "").strip()
    if not name:
        return None
    if name in _GENERIC_WRAPPER_FAMILY_FALLBACK:
        return _GENERIC_WRAPPER_FAMILY_FALLBACK[name]
    for key, value in _GENERIC_WRAPPER_FAMILY_FALLBACK.items():
        if key.endswith(":*"):
            prefix = key[:-1]
            if name.startswith(prefix):
                return value
    return None


TOOLSPEC_BLAST_RADIUS_ATTR = "blast_radius"
TOOLSPEC_SANDBOX_KIND_ATTR = "sandbox_kind"


_MIN_SCOPE_DEFAULT_RADIUS: Mapping[str, ToolBlastRadius] = MappingProxyType(
    {
        "READ_ONLY": "read_only",
        "WRITE_SAFE": "local_mutation",
        "POWER_USER": "code_execution",
        "UI_AUTOMATION": "local_mutation",
    }
)


def min_scope_default_radius() -> Mapping[str, ToolBlastRadius]:
    """Return the frozen min_scope → blast_radius default map."""
    return _MIN_SCOPE_DEFAULT_RADIUS


def _validate_blast_radius(radius: Any) -> ToolBlastRadius:
    if radius not in _BLAST_RADIUS_RANK:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"unknown ToolBlastRadius value: {radius!r}; "
            f"must be one of {TOOL_BLAST_RADIUSES!r}",
            {"radius": str(radius)},
        )
    return radius  # type: ignore[return-value]


def _validate_sandbox_kind(kind: Any) -> SandboxKind:
    if kind not in SANDBOX_KINDS:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"unknown SandboxKind value: {kind!r}; must be one of {SANDBOX_KINDS!r}",
            {"kind": str(kind)},
        )
    return kind  # type: ignore[return-value]


def _credential_escalates_radius(credential_ref: Optional[CredentialRef]) -> bool:
    if credential_ref is None:
        return False
    return credential_ref.scope_kind != "process"


def classify_tool_blast_radius(
    tool_spec: Any,
    *,
    credential_ref: Optional[CredentialRef] = None,
) -> ToolBlastRadiusProfile:
    """Classify one tool into a :class:`ToolBlastRadiusProfile`."""
    name = str(getattr(tool_spec, "name", "") or "").strip()
    if not name:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "tool_spec.name is required for blast-radius classification",
        )

    declared_radius = getattr(tool_spec, TOOLSPEC_BLAST_RADIUS_ATTR, None)
    declared_sandbox = getattr(tool_spec, TOOLSPEC_SANDBOX_KIND_ATTR, None)

    family_fallback = _resolve_family_fallback(name)

    radius: ToolBlastRadius
    if declared_radius is not None:
        radius = _validate_blast_radius(declared_radius)
    elif family_fallback is not None:
        radius = family_fallback[0]
    else:
        min_scope = str(getattr(tool_spec, "min_scope", "") or "").strip()
        radius = _MIN_SCOPE_DEFAULT_RADIUS.get(min_scope, "read_only")
        if bool(getattr(tool_spec, "dangerous", False)):
            if blast_radius_rank(radius) < blast_radius_rank("local_mutation"):
                radius = "local_mutation"

    if _credential_escalates_radius(credential_ref):
        if blast_radius_rank(radius) < blast_radius_rank("remote_mutation"):
            radius = "remote_mutation"

    sandbox: SandboxKind
    if declared_sandbox is not None:
        sandbox = _validate_sandbox_kind(declared_sandbox)
    elif family_fallback is not None:
        sandbox = family_fallback[1]
    else:
        sandbox = "runtime_local"

    return ToolBlastRadiusProfile(
        tool_name=name,
        blast_radius=radius,
        sandbox_kind=sandbox,
        credential_ref=credential_ref,
        dangerous=bool(getattr(tool_spec, "dangerous", False)),
        notes="",
    )


def classify_composed_blast_radius(
    profiles: Sequence[ToolBlastRadiusProfile],
) -> ToolBlastRadius:
    """Compose a sequence of profiles into one composed blast radius."""
    if not profiles:
        return "read_only"
    top: ToolBlastRadius = "read_only"
    top_rank = blast_radius_rank(top)
    for profile in profiles:
        candidate = _validate_blast_radius(profile.blast_radius)
        rank = blast_radius_rank(candidate)
        if rank > top_rank:
            top = candidate
            top_rank = rank
    return top


def _composition_transition(
    prior_profiles: Sequence[ToolBlastRadiusProfile],
    next_profile: ToolBlastRadiusProfile,
) -> tuple[ToolBlastRadius, ToolBlastRadius]:
    """Return the typed (from, to) transition for a composition step."""
    from_radius = classify_composed_blast_radius(prior_profiles)
    composed_after = classify_composed_blast_radius(
        tuple(prior_profiles) + (next_profile,)
    )
    return from_radius, composed_after


def _composition_decision(
    policy: CompositionPolicy,
    transition: tuple[ToolBlastRadius, ToolBlastRadius],
    composed_after: ToolBlastRadius,
) -> CompositionDecision:
    """Resolve the typed decision for a composition transition."""
    if transition in policy.forbidden_transitions:
        return "forbidden"
    if policy.max_radius_per_turn is not None:
        cap_rank = blast_radius_rank(policy.max_radius_per_turn)
        if blast_radius_rank(composed_after) > cap_rank:
            return "forbidden"
    if transition in policy.escalation_required_transitions:
        return "escalation_required"
    return "allowed"


def requires_composition_approval(
    policy: CompositionPolicy,
    prior_profiles: Sequence[ToolBlastRadiusProfile],
    next_profile: ToolBlastRadiusProfile,
) -> bool:
    """Pure guard: return True iff the transition requires operator approval."""
    if not policy.frozen:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"CompositionPolicy {policy.policy_id!r} is not marked frozen; "
            "policies must be constructed frozen.",
            {"policy_id": policy.policy_id},
        )
    transition = _composition_transition(prior_profiles, next_profile)
    composed_after = transition[1]
    decision = _composition_decision(policy, transition, composed_after)
    return decision in ("escalation_required", "forbidden")


def record_composition_boundary_event(
    policy: CompositionPolicy,
    prior_profiles: Sequence[ToolBlastRadiusProfile],
    next_profile: ToolBlastRadiusProfile,
    *,
    seam_id: str,
    audit_log: CompositionAuditLog,
) -> CompositionBoundaryEvent:
    """Emit a :class:`CompositionBoundaryEvent` to the audit log."""
    if not policy.frozen:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            f"CompositionPolicy {policy.policy_id!r} is not marked frozen; "
            "policies must be constructed frozen.",
            {"policy_id": policy.policy_id},
        )
    label = str(seam_id or "").strip()
    if not label:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "seam_id must be a caller-declared static label; "
            "the seam never synthesizes it.",
        )

    transition = _composition_transition(prior_profiles, next_profile)
    from_radius, to_radius = transition
    decision = _composition_decision(policy, transition, to_radius)

    tool_names: tuple[str, ...] = tuple(
        profile.tool_name for profile in prior_profiles
    ) + (next_profile.tool_name,)

    event = CompositionBoundaryEvent(
        event_id=str(uuid.uuid4()),
        policy_id=policy.policy_id,
        from_radius=from_radius,
        to_radius=to_radius,
        tool_names=tool_names,
        seam_id=label,
        decision=decision,
        recorded_at=_now_iso(),
    )
    audit_log.append(event)
    return event


def build_composition_policy(
    *,
    policy_id: str,
    forbidden_transitions: Iterable[tuple[ToolBlastRadius, ToolBlastRadius]] = (),
    escalation_required_transitions: Iterable[
        tuple[ToolBlastRadius, ToolBlastRadius]
    ] = (),
    max_radius_per_turn: Optional[ToolBlastRadius] = None,
) -> CompositionPolicy:
    """Build a typed :class:`CompositionPolicy` frozen at construction."""
    label = str(policy_id or "").strip()
    if not label:
        raise ToolRuntimeError(
            "INVALID_ARGUMENT",
            "policy_id must be a caller-declared static label.",
        )

    def _normalize(
        pairs: Iterable[tuple[ToolBlastRadius, ToolBlastRadius]],
    ) -> frozenset[tuple[ToolBlastRadius, ToolBlastRadius]]:
        out: list[tuple[ToolBlastRadius, ToolBlastRadius]] = []
        for pair in pairs:
            if len(pair) != 2:
                raise ToolRuntimeError(
                    "INVALID_ARGUMENT",
                    f"composition transition must be a (from, to) pair: {pair!r}",
                    {"pair": str(pair)},
                )
            from_r, to_r = pair
            out.append((_validate_blast_radius(from_r), _validate_blast_radius(to_r)))
        return frozenset(out)

    if max_radius_per_turn is not None:
        max_radius_per_turn = _validate_blast_radius(max_radius_per_turn)

    return CompositionPolicy(
        policy_id=label,
        forbidden_transitions=_normalize(forbidden_transitions),
        escalation_required_transitions=_normalize(escalation_required_transitions),
        max_radius_per_turn=max_radius_per_turn,
        frozen=True,
    )


@dataclass
class InMemoryCompositionAuditLog:
    """Simple FIFO-per-turn audit log used by tests and default wiring."""

    events: list[CompositionBoundaryEvent] = field(default_factory=list)

    def append(self, event: CompositionBoundaryEvent) -> None:
        self.events.append(event)

    def boundary_events(self) -> tuple[CompositionBoundaryEvent, ...]:
        return tuple(self.events)


__all__ = [
    "COMPOSITION_DECISIONS",
    "CompositionAuditLog",
    "CompositionBoundaryEvent",
    "CompositionDecision",
    "CompositionPolicy",
    "InMemoryCompositionAuditLog",
    "SANDBOX_KINDS",
    "SandboxKind",
    "TOOL_BLAST_RADIUSES",
    "TOOLSPEC_BLAST_RADIUS_ATTR",
    "TOOLSPEC_SANDBOX_KIND_ATTR",
    "ToolBlastRadius",
    "ToolBlastRadiusProfile",
    "blast_radius_rank",
    "build_composition_policy",
    "classify_composed_blast_radius",
    "classify_tool_blast_radius",
    "generic_wrapper_family_fallback",
    "min_scope_default_radius",
    "record_composition_boundary_event",
    "requires_composition_approval",
]
