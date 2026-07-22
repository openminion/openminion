from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol
from openminion.base.constants import STATE_KEY_WORKING

ReplayUseCase = Literal[
    "debug",
    "regression_test",
    "state_recovery",
    "audit_replay",
]

DivergenceKind = Literal[
    "llm_payload_mismatch",
    "tool_payload_mismatch",
    "state_mismatch",
    "event_order_mismatch",
    "missing_event",
]

REPLAY_USE_CASES: tuple[ReplayUseCase, ...] = (
    "debug",
    "regression_test",
    "state_recovery",
    "audit_replay",
)

DIVERGENCE_KINDS: tuple[DivergenceKind, ...] = (
    "llm_payload_mismatch",
    "tool_payload_mismatch",
    "state_mismatch",
    "event_order_mismatch",
    "missing_event",
)

REPLAY_DIVERGENCE_EVENT_TYPE = "runtime.replay_divergence"


@dataclass(frozen=True)
class ReplayPolicy:
    """Typed per-use-case replay policy."""

    use_case: ReplayUseCase
    stop_on_divergence: bool = False
    compare_llm_payloads: bool = True
    compare_tool_payloads: bool = True
    deterministic_time: bool = True
    deterministic_random: bool = True


@dataclass(frozen=True)
class ReplayBundle:
    """Typed read-only bundle for deterministic replay."""

    use_case: ReplayUseCase
    initial_state: Mapping[str, Any]
    event_log: tuple[Mapping[str, Any], ...]
    policy: ReplayPolicy
    bundle_id: str
    recorded_at: datetime
    expected_state: Mapping[str, Any] | None = None
    expected_event_payloads: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class ReplayDivergence:
    """Typed divergence record emitted by ``replay_from_events``."""

    event_id: str
    seam_id: str
    expected_payload: Mapping[str, Any]
    actual_payload: Mapping[str, Any]
    divergence_kind: DivergenceKind
    recorded_at: datetime


@dataclass(frozen=True)
class ReplayResult:
    """Typed replay outcome."""

    bundle_id: str
    final_state: Mapping[str, Any]
    divergences: tuple[ReplayDivergence, ...]
    events_replayed: int
    events_skipped: int
    completed_at: datetime


class ReplayDivergenceLog(Protocol):
    """Canonical-events adapter for ``ReplayDivergence`` emission."""

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None = None,
    ) -> str: ...


_DEFAULT_POLICIES: Mapping[ReplayUseCase, ReplayPolicy] = MappingProxyType(
    {
        "debug": ReplayPolicy(
            use_case="debug",
            stop_on_divergence=False,
            compare_llm_payloads=True,
            compare_tool_payloads=True,
        ),
        "regression_test": ReplayPolicy(
            use_case="regression_test",
            stop_on_divergence=True,
            compare_llm_payloads=True,
            compare_tool_payloads=True,
        ),
        "state_recovery": ReplayPolicy(
            use_case="state_recovery",
            stop_on_divergence=False,
            compare_llm_payloads=False,
            compare_tool_payloads=False,
        ),
        "audit_replay": ReplayPolicy(
            use_case="audit_replay",
            stop_on_divergence=False,
            compare_llm_payloads=True,
            compare_tool_payloads=True,
        ),
    }
)


def default_policy_for(use_case: ReplayUseCase) -> ReplayPolicy:
    """Return the canonical ``ReplayPolicy`` for ``use_case``."""

    return _DEFAULT_POLICIES[use_case]


def _event_kind_to_divergence(event_type: str) -> DivergenceKind:
    """Map a canonical event type onto its typed ``DivergenceKind``."""

    if event_type.startswith("llm."):
        return "llm_payload_mismatch"
    if event_type.startswith("tool."):
        return "tool_payload_mismatch"
    return "state_mismatch"


def _payload_comparison_enabled(policy: ReplayPolicy, kind: DivergenceKind) -> bool:
    if kind == "llm_payload_mismatch":
        return policy.compare_llm_payloads
    if kind == "tool_payload_mismatch":
        return policy.compare_tool_payloads
    return True


def _event_timestamp(event: Mapping[str, Any], default: datetime) -> datetime:
    raw = event.get("timestamp")
    if isinstance(raw, datetime):
        return raw
    return default


def replay_from_events(bundle: ReplayBundle) -> ReplayResult:
    """Deterministically replay ``bundle.event_log`` against ``initial_state``."""

    divergences: list[ReplayDivergence] = []
    events_replayed = 0
    events_skipped = 0
    last_seq: int | None = None
    last_timestamp = bundle.recorded_at
    halted = False

    for event in bundle.event_log:
        if halted:
            events_skipped += 1
            continue

        event_id = str(event.get("event_id") or "")
        event_type = str(event.get("event_type") or "")
        seq_raw = event.get("seq")
        seq = int(seq_raw) if isinstance(seq_raw, int) else None
        ts = _event_timestamp(event, last_timestamp)

        if seq is not None and last_seq is not None and seq < last_seq:
            halted = _record_replayed_divergence(
                divergences,
                divergence=_event_order_divergence(
                    event_id=event_id,
                    event_type=event_type,
                    last_seq=last_seq,
                    seq=seq,
                    recorded_at=ts,
                ),
                policy=bundle.policy,
            )
            events_replayed += 1
            last_seq = seq
            last_timestamp = ts
            continue

        actual_payload = event.get("payload")
        if not isinstance(actual_payload, Mapping):
            actual_payload = {}

        expected_payload = bundle.expected_event_payloads.get(event_id)
        divergence = _event_payload_divergence(
            event_id=event_id,
            event_type=event_type,
            actual_payload=actual_payload,
            expected_payload=expected_payload,
            policy=bundle.policy,
            recorded_at=ts,
        )
        if divergence is not None:
            halted = _record_replayed_divergence(
                divergences,
                divergence=divergence,
                policy=bundle.policy,
            )
            events_replayed += 1
            last_seq = seq if seq is not None else last_seq
            last_timestamp = ts
            continue

        if event.get("missing") is True:
            halted = _record_replayed_divergence(
                divergences,
                divergence=_missing_event_divergence(
                    event_id=event_id,
                    event_type=event_type,
                    expected_payload=expected_payload,
                    recorded_at=ts,
                ),
                policy=bundle.policy,
            )
            events_replayed += 1
            last_seq = seq if seq is not None else last_seq
            last_timestamp = ts
            continue

        events_replayed += 1
        last_seq = seq if seq is not None else last_seq
        last_timestamp = ts

    final_state: Mapping[str, Any] = dict(bundle.initial_state)
    final_divergence = _final_state_divergence(
        expected_state=bundle.expected_state,
        final_state=final_state,
        halted=halted,
        recorded_at=last_timestamp,
    )
    if final_divergence is not None:
        divergences.append(final_divergence)

    return ReplayResult(
        bundle_id=bundle.bundle_id,
        final_state=final_state,
        divergences=tuple(divergences),
        events_replayed=events_replayed,
        events_skipped=events_skipped,
        completed_at=last_timestamp,
    )


def _final_state_divergence(
    *,
    expected_state: Mapping[str, Any] | None,
    final_state: Mapping[str, Any],
    halted: bool,
    recorded_at: datetime,
) -> ReplayDivergence | None:
    if expected_state is None or halted:
        return None
    if dict(expected_state) == dict(final_state):
        return None
    return ReplayDivergence(
        event_id="<final-state>",
        seam_id=STATE_KEY_WORKING,
        expected_payload=dict(expected_state),
        actual_payload=dict(final_state),
        divergence_kind="state_mismatch",
        recorded_at=recorded_at,
    )


def _missing_event_divergence(
    *,
    event_id: str,
    event_type: str,
    expected_payload: Mapping[str, Any] | None,
    recorded_at: datetime,
) -> ReplayDivergence:
    return ReplayDivergence(
        event_id=event_id,
        seam_id=event_type or "unknown",
        expected_payload=dict(expected_payload) if expected_payload else {},
        actual_payload={},
        divergence_kind="missing_event",
        recorded_at=recorded_at,
    )


def _event_order_divergence(
    *,
    event_id: str,
    event_type: str,
    last_seq: int,
    seq: int,
    recorded_at: datetime,
) -> ReplayDivergence:
    return ReplayDivergence(
        event_id=event_id,
        seam_id=event_type or "unknown",
        expected_payload={"seq": last_seq + 1},
        actual_payload={"seq": seq},
        divergence_kind="event_order_mismatch",
        recorded_at=recorded_at,
    )


def _event_payload_divergence(
    *,
    event_id: str,
    event_type: str,
    actual_payload: Mapping[str, Any],
    expected_payload: Mapping[str, Any] | None,
    policy: ReplayPolicy,
    recorded_at: datetime,
) -> ReplayDivergence | None:
    if expected_payload is None:
        return None
    kind = _event_kind_to_divergence(event_type)
    if not _payload_comparison_enabled(policy, kind):
        return None
    if dict(expected_payload) == dict(actual_payload):
        return None
    return ReplayDivergence(
        event_id=event_id,
        seam_id=event_type or "unknown",
        expected_payload=dict(expected_payload),
        actual_payload=dict(actual_payload),
        divergence_kind=kind,
        recorded_at=recorded_at,
    )


def _record_replayed_divergence(
    divergences: list[ReplayDivergence],
    *,
    divergence: ReplayDivergence,
    policy: ReplayPolicy,
) -> bool:
    divergences.append(divergence)
    return bool(policy.stop_on_divergence)


def record_replay_divergence(
    divergence: ReplayDivergence,
    *,
    divergence_log: ReplayDivergenceLog,
    trace_id: str | None = None,
) -> str:
    """Emit a typed ``ReplayDivergence`` onto the canonical-events stream."""

    if divergence.divergence_kind not in DIVERGENCE_KINDS:
        raise ValueError(
            f"unknown divergence kind: {divergence.divergence_kind!r}; "
            f"must be one of {DIVERGENCE_KINDS}"
        )
    payload: dict[str, Any] = {
        "event_id": divergence.event_id,
        "seam_id": divergence.seam_id,
        "divergence_kind": divergence.divergence_kind,
        "expected_payload": dict(divergence.expected_payload),
        "actual_payload": dict(divergence.actual_payload),
        "recorded_at": divergence.recorded_at.isoformat(),
    }
    return divergence_log.emit(
        REPLAY_DIVERGENCE_EVENT_TYPE,
        payload,
        trace_id=trace_id,
    )


def utcnow() -> datetime:
    """Helper to construct timezone-aware UTC timestamps inside the seam."""

    return datetime.now(timezone.utc)


__all__ = [
    "DIVERGENCE_KINDS",
    "DivergenceKind",
    "REPLAY_DIVERGENCE_EVENT_TYPE",
    "REPLAY_USE_CASES",
    "ReplayBundle",
    "ReplayDivergence",
    "ReplayDivergenceLog",
    "ReplayPolicy",
    "ReplayResult",
    "ReplayUseCase",
    "default_policy_for",
    "record_replay_divergence",
    "replay_from_events",
    "utcnow",
]
