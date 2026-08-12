"""Canonical OpenMinion session facts for delegated memory lifecycle."""

from __future__ import annotations

from typing import Any, Literal, Protocol, get_args

from openminion.modules.memory.errors import InvalidArgumentError

DelegatedMemorySessionEventType = Literal[
    "memory.delegation.grant_resolved",
    "memory.delegation.access_denied",
    "memory.delegation.candidate_handed_back",
    "memory.delegation.grant_revoked",
]

_EVENT_TYPES = frozenset(get_args(DelegatedMemorySessionEventType))


class CanonicalSessionEventSink(Protocol):
    def emit_canonical_event(
        self,
        session_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str: ...


def emit_delegated_memory_session_event(
    sink: CanonicalSessionEventSink,
    *,
    session_id: str,
    event_type: DelegatedMemorySessionEventType,
    operation: str,
    reason: str,
    grant_ref: str | None = None,
    evidence_refs: tuple[str, ...] = (),
) -> str:
    """Persist one replay fact without content, queries, tokens, or credentials."""

    if event_type not in _EVENT_TYPES:
        raise InvalidArgumentError("invalid delegated memory session event")
    return sink.emit_canonical_event(
        session_id,
        event_type,
        {
            "operation": operation,
            "reason": reason,
            "grant_ref": grant_ref,
            "evidence_refs": list(evidence_refs),
        },
    )


__all__ = [
    "CanonicalSessionEventSink",
    "DelegatedMemorySessionEventType",
    "emit_delegated_memory_session_event",
]
